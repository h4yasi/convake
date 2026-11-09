"""
プレミアム会員機能（手動確認＋コマンド登録方式）。

フロー:
1. サポートサーバーに常設するパネル（Embed+ボタン、!premium_panelで設置）から
   「購入する」を押すと、PayPay送金リンクが表示される。
2. ユーザーが送金後「支払いました」ボタンを押すと、管理者（通知チャンネル or Bot所有者DM）に通知される。
3. 管理者はPayPayアプリで入金を目視確認し、 !premium_grant で手動登録する。
4. 期限が近づいたらDMでリマインド、期限が切れたら自動でロール剥奪・登録削除される。

「支払い完了を自動検知する」部分だけは個人間送金の仕組み上どうしても自動化できないため、
そこだけ人間の確認を挟むハイブリッド構成になっている。

管理コマンド（!premium_panel, !premium_grant, !premium_revoke, !premium_list）は
あえてスラッシュコマンドにせず!プレフィックスのメッセージコマンドにしている
（Discordのスラッシュコマンド一覧に誰でも見える形で並んでしまうのを避けるため）。
"""
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    PREMIUM_ROLE_ID,
    PREMIUM_PRICE_JPY,
    PAYPAY_LINK,
    PREMIUM_ADMIN_NOTIFY_CHANNEL_ID,
    PREMIUM_REMINDER_DAYS_BEFORE,
    PREMIUM_PROMOTION_MESSAGE,
    LINK_RETENTION_DAYS_FREE,
    LINK_RETENTION_DAYS_PREMIUM,
)
from utils import premium_store, status_tracker
from cogs.link import format_days
from utils.permissions import owner_only

# 「支払いました」ボタンは誰でも押せる（購入前提のフローのため未登録ユーザーでも押せてしまう）。
# 連打で管理者への通知（DM/通知チャンネル）を無限に送りつけられるスパムを防ぐため、
# ユーザーごとに最低この秒数は間隔を空けさせる。
PAYMENT_REPORT_COOLDOWN_SECONDS = 120
_last_payment_report: dict[int, float] = {}
logger = logging.getLogger("discord_bot")


def _parse_utc(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class PaidReportView(discord.ui.View):
    """購入者が「支払いました」を報告するための一時的なView（永続化不要）。"""

    def __init__(self, cog: "PremiumCog", buyer_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.buyer_id = buyer_id

    @discord.ui.button(label="支払いました", style=discord.ButtonStyle.success, emoji="✅")
    async def paid(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("このボタンは購入者本人のみ使用できます。", ephemeral=True)
            return

        # 連打スパム対策: 同じユーザーからの通知は一定間隔を空ける
        now = time.monotonic()
        last = _last_payment_report.get(interaction.user.id)
        if last is not None and now - last < PAYMENT_REPORT_COOLDOWN_SECONDS:
            remaining = PAYMENT_REPORT_COOLDOWN_SECONDS - (now - last)
            await interaction.response.send_message(
                f"すでに購入済みです。{remaining:.0f}秒後にもう一度お試しください。", ephemeral=True
            )
            return
        _last_payment_report[interaction.user.id] = now
        if len(_last_payment_report) > 1_000:
            cutoff = now - PAYMENT_REPORT_COOLDOWN_SECONDS
            stale = [uid for uid, ts in _last_payment_report.items() if ts < cutoff]
            for uid in stale:
                del _last_payment_report[uid]

        button.disabled = True
        button.label = "報告済み"
        await interaction.response.edit_message(view=self)
        if self.cog is not None:
            await self.cog.notify_admin_payment(interaction.user)
        await interaction.followup.send(
            "ご購入ありがとうございます。入金が確認でき次第プレミアム会員として追加されます。少々お待ちください。",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        status_tracker.record_error(f"paid report view: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
            else:
                await interaction.response.send_message("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
        except (discord.HTTPException, discord.InteractionResponded):
            pass


class PremiumPanelView(discord.ui.View):
    """
    サポートサーバーに常設するパネル用View。
    timeout=None（永続化）にしているため、Bot再起動後も動き続ける
    （cog_loadでbot.add_view()に登録することで再起動後も機能する）。
    custom_idは固定文字列にする必要がある（動的だと再起動後に紐づかない）。
    """

    def __init__(self, cog: "PremiumCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="購入する",
        style=discord.ButtonStyle.primary,
        emoji="💸",
        custom_id="premium_panel:buy",
    )
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="プレミアム会員購入",
            description=(
                f"**{PREMIUM_PRICE_JPY}円 / 1ヶ月**\n"
                "動画⇔GIF変換・DM利用・応答の非公開設定・優先処理・リンク履歴の再発行などが使えるようになります。"
            ),
            color=discord.Color.gold(),
        )
        link_text = PAYPAY_LINK if PAYPAY_LINK else "（リンク未設定。運営にお問い合わせください）"
        embed.add_field(name="お支払いリンク（PayPay）", value=link_text, inline=False)
        embed.set_footer(text="送金後、下に表示される「支払いました」ボタンを押してください。")
        await interaction.response.send_message(
            embed=embed,
            view=PaidReportView(self.cog, interaction.user.id),
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        status_tracker.record_error(f"premium panel view: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
            else:
                await interaction.response.send_message("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
        except (discord.HTTPException, discord.InteractionResponded):
            pass


class PremiumCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # 再起動後もパネルのボタンが機能するよう、persistent viewとして再登録する
        self.bot.add_view(PremiumPanelView(self))
        self._check_expirations.start()

    async def cog_unload(self):
        self._check_expirations.cancel()

    # --- 支払い報告の通知 ---

    async def notify_admin_payment(self, user: discord.abc.User):
        text = (
            f"支払い報告を受け取りました: {user.mention} (`{user.id}`)\n"
            f"PayPayで入金を確認できたら付与してください"
        )
        channel = None
        if PREMIUM_ADMIN_NOTIFY_CHANNEL_ID:
            channel = self.bot.get_channel(PREMIUM_ADMIN_NOTIFY_CHANNEL_ID)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(PREMIUM_ADMIN_NOTIFY_CHANNEL_ID)
                except discord.HTTPException:
                    channel = None
        if channel is not None and hasattr(channel, "send"):
            try:
                await channel.send(text)
                return
            except discord.HTTPException as e:
                logger.warning("通知チャンネル(%s)への送信に失敗したためオーナーDMにフォールバックします: %s", PREMIUM_ADMIN_NOTIFY_CHANNEL_ID, e)

        # 通知チャンネル未設定・送信失敗時は、Botのアプリ所有者にDMでフォールバック
        try:
            app_info = await self.bot.application_info()
            owner = app_info.owner
            if owner is not None:
                await owner.send(text)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # --- 管理者向けコマンド（あえてスラッシュコマンドではなく!プレフィックスにしている。
    #     スラッシュコマンドはDiscordのコマンド一覧に誰でも見える形で並ぶため、
    #     オーナー専用の管理コマンドの存在自体を一般ユーザーに気付かせないための措置） ---

    @commands.command(name="premium_panel")
    @owner_only()
    async def premium_panel(self, ctx: commands.Context):
        """プレミアム購入パネルをこのチャンネルに設置します（オーナー限定, !premium_panel）"""
        embed = discord.Embed(
            title="プレミアム会員",
            description=(
                f"**料金: {PREMIUM_PRICE_JPY}円 / 1ヶ月**\n\n"
                "下のボタンから購入手続きができます。"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="プレミアム特典",
            value=(
                "動画⇔GIF変換が使えるようになります（動画は15秒まで）\n"
                "`/merge`の画像結合枚数が3枚→5枚に増えます\n"
                "DMでもコマンドが使えるようになります\n"
                "`/premium_settings`で、コマンドの応答を自分にだけ見える形に設定できます\n"
                "混雑時、コマンドの処理が優先されます（無料会員より先に順番が回ってきます）\n"
                "コマンドを連続して使える間隔が短くなります\n"
                f"`/link`の履歴を{format_days(LINK_RETENTION_DAYS_PREMIUM)}保存・再発行できます"
                f"（無料会員は{format_days(LINK_RETENTION_DAYS_FREE)}）。保存件数も多くなります"
            ),
            inline=False,
        )
        await ctx.channel.send(embed=embed, view=PremiumPanelView(self))
        await ctx.message.add_reaction("✅")

    @commands.command(name="premium_grant")
    @owner_only()
    async def premium_grant(self, ctx: commands.Context, user: discord.Member, months: int = 1):
        """支払い確認後、ユーザーをプレミアムとして登録します（オーナー限定）
        使い方: !premium_grant @ユーザー [月数（省略時1）]
        """
        if ctx.guild is None:
            await ctx.send("ロール付与のためこのコマンドはサーバー内で実行してください。")
            return
        months = max(1, min(months, 24))

        # 判定の実体はここ（ユーザーID単位）。Botがどのサーバーにいても、DMでも、
        # このユーザーIDである限りプレミアムとして扱われる。
        expires_at_iso = await premium_store.grant(user.id, ctx.guild.id, months)

        # ロールは「このサーバー内での見た目のバッジ」的な任意機能。
        # PREMIUM_ROLE_IDが未設定でも、上のgrant()自体は成功しているので機能に支障はない。
        role_note = ""
        if PREMIUM_ROLE_ID:
            role = ctx.guild.get_role(PREMIUM_ROLE_ID)
            if role is not None:
                try:
                    await user.add_roles(role, reason="プレミアム登録")
                except discord.Forbidden:
                    role_note = "\nロール付与には失敗しました。Botの権限/ロールの位置関係を確認してください"

        expires_dt = _parse_utc(expires_at_iso)
        await ctx.send(
            f"✅ {user.mention} をプレミアムに登録しました。期限: {discord.utils.format_dt(expires_dt, style='F')}"
            f"{role_note}"
        )
        try:
            await user.send(
                "プレミアム会員登録が完了しました\n"
                f"有効期限: {discord.utils.format_dt(expires_dt, style='F')}\n"
                
                "期限が近づいたら再度DMでお知らせします。"
            )
        except discord.Forbidden:
            pass

    @commands.command(name="premium_revoke")
    @owner_only()
    async def premium_revoke(self, ctx: commands.Context, user: discord.Member):
        """ユーザーのプレミアムを手動で解除します（オーナー限定）
        使い方: !premium_revoke @ユーザー
        """
        removed = await premium_store.revoke(user.id)
        if PREMIUM_ROLE_ID and ctx.guild is not None:
            role = ctx.guild.get_role(PREMIUM_ROLE_ID)
            if role is not None and role in user.roles:
                try:
                    await user.remove_roles(role, reason="プレミアム手動解除")
                except discord.Forbidden:
                    pass
        await ctx.send("解除しました。" if removed else "登録データは見つかりませんでした。")

    @commands.command(name="premium_list")
    @owner_only()
    async def premium_list(self, ctx: commands.Context):
        """現在のプレミアム会員一覧を表示します（オーナー限定）"""
        data = await premium_store.get_all()
        if not data:
            await ctx.send("現在プレミアム会員はいません。")
            return

        lines = []
        for uid, info in data.items():
            try:
                expires_dt = _parse_utc(info["expires_at"])
                lines.append(f"<@{uid}> — 期限: {discord.utils.format_dt(expires_dt, style='R')}")
            except (KeyError, ValueError, TypeError):
                lines.append(f"<@{uid}> — 期限不明")
        await ctx.send("**プレミアム会員一覧**\n" + "\n".join(lines))

    # --- プレミアム会員本人向けの設定コマンド ---

    @app_commands.command(
        name="premium_settings",
        description="プレミアム会員向け設定",
    )
    @app_commands.describe(ephemeral="有効にすると、以後すべてのコマンドの応答が自分にだけ見える形になります")
    async def premium_settings(self, interaction: discord.Interaction, ephemeral: bool):
        # これは管理者専用コマンドではなく、プレミアム会員本人が自分の設定を変えるためのコマンド。
        # 非会員は使えず、その場合はどこでコマンドを使っても応答は常に公開（誰でも見える）のまま。
        if not await premium_store.is_active(interaction.user.id):
            embed = discord.Embed(
                description=f"このコマンドはプレミアム会員限定です。\n{PREMIUM_PROMOTION_MESSAGE}",
                color=discord.Color.gold(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await premium_store.set_ephemeral_preference(interaction.user.id, ephemeral)
        state_text = "自分にだけ見える形（ephemeral）" if ephemeral else "誰でも見える形（公開）"
        await interaction.response.send_message(
            f"設定を更新しました。今後、コマンドの応答は{state_text}になります。",
            ephemeral=True,
        )

    # --- 期限管理（リマインドDM・自動剥奪） ---

    @tasks.loop(hours=24)
    async def _check_expirations(self):
        try:
            data = await premium_store.get_all()
            now = datetime.now(timezone.utc)

            for uid, info in list(data.items()):
                try:
                    expires_at = _parse_utc(info["expires_at"])
                except (KeyError, ValueError, TypeError):
                    continue
                guild_id = info.get("guild_id")

                if expires_at <= now:
                    await self._expire_member(int(uid), guild_id)
                    continue

                remaining = expires_at - now
                if remaining <= timedelta(days=PREMIUM_REMINDER_DAYS_BEFORE) and not info.get("reminded"):
                    await self._send_reminder(int(uid), expires_at)
                    await premium_store.mark_reminded(int(uid))
        except Exception:
            logger.exception("プレミアム期限チェック中にエラーが発生しました")

    @_check_expirations.before_loop
    async def _before_check_expirations(self):
        await self.bot.wait_until_ready()

    async def _send_reminder(self, user_id: int, expires_at: datetime):
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(
                "プレミアム会員の有効期限が近づいています。\n"
                f"期限: {discord.utils.format_dt(expires_at, style='F')}\n"
                "継続する場合は、購入パネルから改めてお手続きください。"
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _expire_member(self, user_id: int, guild_id: Optional[int]):
        await premium_store.revoke(user_id)
        # ロールは任意機能。設定されている場合のみ、登録時のサーバーから剥奪を試みる
        # （判定自体はpremium_store側のデータ削除だけで、どのサーバーでも反映される）。
        if guild_id and PREMIUM_ROLE_ID:
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                try:
                    member = guild.get_member(user_id) or await guild.fetch_member(user_id)
                except (discord.NotFound, discord.HTTPException):
                    member = None
                role = guild.get_role(PREMIUM_ROLE_ID)
                if member is not None and role is not None and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="プレミアム期限切れ")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(
                "プレミアム会員の期限が切れました。ご利用ありがとうございました。\n"
                "再度継続する場合は購入パネルから改めてお手続きください。"
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(PremiumCog(bot))
