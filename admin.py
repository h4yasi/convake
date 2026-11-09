"""
開発者・管理者専用コントロールパネル Cog。
プレミアム会員の付与・剥奪・一覧表示・購入パネル設置・Bot診断・GCメモリ解放を
1つのスラッシュコマンド `/admin` に集約しています。
"""
import gc
import os
import re
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    TEMP_DIR,
    PREMIUM_ROLE_ID,
    PREMIUM_PRICE_JPY,
    PAYPAY_LINK,
    LINK_RETENTION_DAYS_FREE,
    LINK_RETENTION_DAYS_PREMIUM,
)
from cogs.link import format_days
from cogs.premium import PremiumPanelView
from utils import premium_store, status_tracker
from utils.permissions import is_owner
from utils.system_info import get_peak_memory_mb, get_current_memory_mb


def _format_uptime(delta_seconds: float) -> str:
    days, rem = divmod(int(delta_seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}日")
    if hours or days:
        parts.append(f"{hours}時間")
    parts.append(f"{minutes}分")
    return " ".join(parts)


def _get_dir_size_mb(path: str) -> float:
    total = 0
    if not os.path.isdir(path):
        return 0.0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1024 * 1024)


def _parse_utc(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_still_active(info: dict, now: datetime) -> bool:
    try:
        expires_at = _parse_utc(info["expires_at"])
    except (KeyError, ValueError, TypeError):
        return False
    return expires_at > now


def _extract_user_id(raw_input: str) -> Optional[int]:
    """ユーザー入力（ID または <@!123456789> 形式のメンション）から数値IDを抽出する"""
    cleaned = raw_input.strip()
    match = re.search(r"(\d{16,21})", cleaned)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


async def _build_admin_embed(
    bot: commands.Bot,
    last_action: Optional[str] = None,
    collected_gc: Optional[int] = None,
) -> discord.Embed:
    """開発者向けメインコントロールパネルのEmbedを構築する"""
    now = datetime.now(timezone.utc)
    uptime_seconds = (now - status_tracker.get_start_time()).total_seconds()
    recent_errors = status_tracker.get_recent_errors()

    embed = discord.Embed(
        title="🛠️ 開発者コントロールパネル",
        description="Bot運用管理およびプレミアム機能の一元管理コンソールです。",
        color=discord.Color.brand_green() if not recent_errors else discord.Color.orange(),
        timestamp=now,
    )

    # 1. 稼働状況
    ping_ms = f"{bot.latency * 1000:.0f} ms"
    guild_count = len(bot.guilds)
    uptime_str = _format_uptime(uptime_seconds)

    embed.add_field(
        name="📊 システム稼働状態",
        value=(
            f"**状態**: 🟢 正常稼働中\n"
            f"**稼働時間**: `{uptime_str}`\n"
            f"**WebSocket Ping**: `{ping_ms}`\n"
            f"**参加サーバー数**: `{guild_count} サーバー`"
        ),
        inline=False,
    )

    # 2. プレミアム会員の統計
    premium_data = await premium_store.get_all()
    active_count = sum(1 for info in premium_data.values() if _is_still_active(info, now))
    total_records = len(premium_data)

    embed.add_field(
        name="💎 プレミアム会員状況",
        value=(
            f"**有効会員数**: `{active_count} 名` / 累計登録: `{total_records} 件`\n"
            f"下のボタンから付与・解除・一覧表示が可能です。"
        ),
        inline=False,
    )

    # 3. メモリおよびリソース診断
    current_mem = get_current_memory_mb()
    peak_mem = get_peak_memory_mb()
    temp_mb = _get_dir_size_mb(TEMP_DIR)
    object_count = len(gc.get_objects())

    cur_str = f"{current_mem:.1f} MB" if current_mem is not None else "取得不可"
    peak_str = f"{peak_mem:.1f} MB" if peak_mem is not None else "取得不可"

    embed.add_field(
        name="🩺 メモリ & 内部診断",
        value=(
            f"**現在メモリ**: `{cur_str}` (ピーク: `{peak_str}`)\n"
            f"**一時ファイル領域**: `{temp_mb:.2f} MB`\n"
            f"**管理オブジェクト数**: `{object_count:,} 個`"
        ),
        inline=False,
    )

    # GC実行結果通知
    if collected_gc is not None:
        embed.add_field(
            name="🧹 ガベージコレクション結果",
            value=f"`{collected_gc}` 個の到達不能オブジェクトを回収・解放しました。",
            inline=False,
        )

    # 直近の操作通知
    if last_action:
        embed.add_field(name="📢 実行結果", value=last_action, inline=False)

    # エラー情報（あれば）
    if recent_errors:
        lines = [f"<t:{int(ts.timestamp())}:R> `{msg}`" for ts, msg in reversed(recent_errors)]
        embed.add_field(name="⚠️ 直近のエラーログ（最大5件）", value="\n".join(lines), inline=False)

    embed.set_footer(text="開発者専用・非公開（ephemeral）表示")
    return embed


async def _build_members_embed(bot: commands.Bot) -> discord.Embed:
    """プレミアム会員一覧のEmbedを構築する"""
    data = await premium_store.get_all()
    now = datetime.now(timezone.utc)

    embed = discord.Embed(
        title="📋 プレミアム会員一覧",
        color=discord.Color.gold(),
        timestamp=now,
    )

    if not data:
        embed.description = "現在、プレミアム会員として登録されているユーザーはいません。"
        return embed

    lines = []
    active_count = 0
    expired_count = 0

    for uid, info in sorted(data.items(), key=lambda x: x[1].get("expires_at", ""), reverse=True):
        try:
            expires_dt = _parse_utc(info["expires_at"])
            is_active = expires_dt > now
            status_emoji = "🟢" if is_active else "⚪"
            if is_active:
                active_count += 1
            else:
                expired_count += 1

            time_fmt = discord.utils.format_dt(expires_dt, style="F")
            rel_fmt = discord.utils.format_dt(expires_dt, style="R")
            lines.append(f"{status_emoji} <@{uid}> (`{uid}`)\n   期限: {time_fmt} ({rel_fmt})")
        except (KeyError, ValueError, TypeError):
            lines.append(f"⚪ <@{uid}> (`{uid}`)\n   期限: 記録なし / 不明")

    embed.description = (
        f"**有効会員**: `{active_count}名` | **期限切れ**: `{expired_count}名`\n\n"
        + "\n".join(lines[:25])  # Discordフィールド文字数制限対策
    )
    if len(lines) > 25:
        embed.set_footer(text=f"※ 表示件数上限のため、先頭25件を表示しています（全{len(lines)}件）")
    return embed


# ==========================================
# モーダル（ダイアログ入力画面）
# ==========================================

class GrantPremiumModal(discord.ui.Modal, title="💎 プレミアム会員の付与"):
    """プレミアム付与モーダル"""

    user_input = discord.ui.TextInput(
        label="対象ユーザー（ID または メンション）",
        placeholder="例: 1536204304220950530 または @ユーザー",
        required=True,
        min_length=3,
        max_length=64,
    )

    months_input = discord.ui.TextInput(
        label="付与月数（1〜24ヶ月）",
        default="1",
        required=True,
        min_length=1,
        max_length=2,
    )

    note_input = discord.ui.TextInput(
        label="管理メモ / 理由（任意）",
        placeholder="例: PayPay送金確認、イベント景品 など",
        required=False,
        max_length=100,
    )

    def __init__(self, cog: "AdminCog", origin_view: "AdminControlView"):
        super().__init__()
        self.cog = cog
        self.origin_view = origin_view

    async def on_submit(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ この操作はBot開発者専用です。", ephemeral=True)
            return

        user_id = _extract_user_id(self.user_input.value)
        if not user_id:
            await interaction.response.send_message(
                "❌ 有効なユーザーIDまたはメンションを指定してください。", ephemeral=True
            )
            return

        try:
            months = int(self.months_input.value.strip())
            months = max(1, min(months, 24))
        except ValueError:
            await interaction.response.send_message(
                "❌ 月数は 1 から 24 までの整数で指定してください。", ephemeral=True
            )
            return

        # Discordの3秒応答制限を回避するため、入力検証後すぐにdeferする
        await interaction.response.defer()

        # プレミアム有効期限を付与・更新
        guild_id = interaction.guild_id if interaction.guild else 0
        expires_at_iso = await premium_store.grant(user_id, guild_id, months)
        expires_dt = _parse_utc(expires_at_iso)

        # サーバー内にいればロール付与を試みる（任意機能）
        role_note = ""
        if interaction.guild and PREMIUM_ROLE_ID:
            role = interaction.guild.get_role(PREMIUM_ROLE_ID)
            if role:
                member = interaction.guild.get_member(user_id)
                if not member:
                    try:
                        member = await interaction.guild.fetch_member(user_id)
                    except discord.HTTPException:
                        member = None
                if member:
                    try:
                        await member.add_roles(role, reason="管理者パネルからのプレミアム付与")
                    except (discord.Forbidden, discord.HTTPException):
                        role_note = "（※ロール付与権限が不足しています）"

        # 対象ユーザーへのDM通知を試行
        dm_sent = False
        try:
            target_user = await self.cog.bot.fetch_user(user_id)
            if target_user:
                await target_user.send(
                    "🎉 プレミアム会員登録が完了しました！\n"
                    f"有効期限: {discord.utils.format_dt(expires_dt, style='F')}\n"
                    "Botが導入されているサーバーであれば、どこでもプレミアム機能を使えます。\n"
                    "期限が近づいたらこちらのDMでお知らせします。"
                )
                dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            pass

        dm_note = "（DM通知完了）" if dm_sent else "（DM通知スキップ/非許可）"
        action_msg = (
            f"✅ <@{user_id}> にプレミアム **{months}ヶ月** を付与しました。\n"
            f"期限: {discord.utils.format_dt(expires_dt, style='F')} {role_note} {dm_note}"
        )

        embed = await _build_admin_embed(self.cog.bot, last_action=action_msg)
        try:
            await interaction.edit_original_response(embed=embed, view=self.origin_view)
        except discord.HTTPException:
            if interaction.message is not None:
                await interaction.message.edit(embed=embed, view=self.origin_view)
            else:
                await interaction.followup.send(embed=embed, view=self.origin_view, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        status_tracker.record_error(f"/admin grant modal: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
            else:
                await interaction.response.send_message("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
        except (discord.HTTPException, discord.InteractionResponded):
            pass


class RevokePremiumModal(discord.ui.Modal, title="❌ プレミアム会員の解除"):
    """プレミアム剥奪モーダル"""

    user_input = discord.ui.TextInput(
        label="解除対象ユーザー（ID または メンション）",
        placeholder="例: 1536204304220950530 または @ユーザー",
        required=True,
        min_length=3,
        max_length=64,
    )

    def __init__(self, cog: "AdminCog", origin_view: "AdminControlView"):
        super().__init__()
        self.cog = cog
        self.origin_view = origin_view

    async def on_submit(self, interaction: discord.Interaction):
        if not is_owner(interaction):
            await interaction.response.send_message("❌ この操作はBot開発者専用です。", ephemeral=True)
            return

        user_id = _extract_user_id(self.user_input.value)
        if not user_id:
            await interaction.response.send_message(
                "❌ 有効なユーザーIDまたはメンションを指定してください。", ephemeral=True
            )
            return

        await interaction.response.defer()

        removed = await premium_store.revoke(user_id)

        # サーバーロール解除
        if interaction.guild and PREMIUM_ROLE_ID:
            role = interaction.guild.get_role(PREMIUM_ROLE_ID)
            if role:
                member = interaction.guild.get_member(user_id)
                if not member:
                    try:
                        member = await interaction.guild.fetch_member(user_id)
                    except discord.HTTPException:
                        member = None
                if member and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="管理者パネルからのプレミアム手動解除")
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        if removed:
            action_msg = f"🗑️ <@{user_id}> のプレミアム登録を解除しました。"
        else:
            action_msg = f"⚠️ <@{user_id}> はプレミアム会員として登録されていませんでした。"

        embed = await _build_admin_embed(self.cog.bot, last_action=action_msg)
        try:
            await interaction.edit_original_response(embed=embed, view=self.origin_view)
        except discord.HTTPException:
            if interaction.message is not None:
                await interaction.message.edit(embed=embed, view=self.origin_view)
            else:
                await interaction.followup.send(embed=embed, view=self.origin_view, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        status_tracker.record_error(f"/admin revoke modal: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
            else:
                await interaction.response.send_message("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
        except (discord.HTTPException, discord.InteractionResponded):
            pass


# ==========================================
# インタラクティブ Views
# ==========================================

class AdminControlView(discord.ui.View):
    """管理者メインコントロールパネルのView"""

    def __init__(self, cog: "AdminCog"):
        super().__init__(timeout=900)  # 15分間操作可能
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_owner(interaction):
            await interaction.response.send_message("❌ この操作はBot開発者専用です。", ephemeral=True)
            return False
        return True

    @discord.ui.button(
        label="プレミアム付与",
        style=discord.ButtonStyle.success,
        emoji="💎",
        row=0,
    )
    async def grant_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GrantPremiumModal(self.cog, self))

    @discord.ui.button(
        label="プレミアム解除",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        row=0,
    )
    async def revoke_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RevokePremiumModal(self.cog, self))

    @discord.ui.button(
        label="会員一覧",
        style=discord.ButtonStyle.primary,
        emoji="📋",
        row=0,
    )
    async def list_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await _build_members_embed(self.cog.bot)
        view = AdminMemberListView(self.cog, self)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(
        label="購入パネル設置",
        style=discord.ButtonStyle.secondary,
        emoji="🛒",
        row=1,
    )
    async def deploy_panel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.channel is None:
            await interaction.response.send_message("チャンネルが取得できませんでした。", ephemeral=True)
            return

        embed = discord.Embed(
            title="✨ プレミアム会員",
            description=(
                f"**料金: {PREMIUM_PRICE_JPY}円 / 1ヶ月**\n\n"
                "下のボタンから購入手続きができます。"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="🎁 プレミアム特典",
            value=(
                "🎬 動画⇔GIF変換が使えるようになります（動画は15秒まで）\n"
                "🖼️ `/merge`の画像結合枚数が3枚→5枚に増えます\n"
                "📩 DMでもコマンドが使えるようになります\n"
                "🔒 `/premium_settings`で、コマンドの応答を自分にだけ見える形に設定できます\n"
                "⚡ 混雑時、コマンドの処理が優先されます（無料会員より先に順番が回ってきます）\n"
                "⏱️ コマンドを連続して使える間隔が短くなります\n"
                f"🔗 `/link`の履歴を{format_days(LINK_RETENTION_DAYS_PREMIUM)}保存・再発行できます"
                f"（無料会員は{format_days(LINK_RETENTION_DAYS_FREE)}）。保存件数も多くなります"
            ),
            inline=False,
        )

        premium_cog = self.cog.bot.get_cog("PremiumCog")
        view = PremiumPanelView(premium_cog)
        try:
            await interaction.channel.send(embed=embed, view=view)
            embed_admin = await _build_admin_embed(
                self.cog.bot, last_action="✅ このチャンネルにプレミアム購入パネルを設置しました。"
            )
            await interaction.response.edit_message(embed=embed_admin, view=self)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "❌ このチャンネルへのメッセージ投稿権限がありません。", ephemeral=True
            )

    @discord.ui.button(
        label="メモリ解放(GC)",
        style=discord.ButtonStyle.secondary,
        emoji="🧹",
        row=1,
    )
    async def gc_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        collected = gc.collect()
        embed = await _build_admin_embed(
            self.cog.bot,
            last_action="🧹 手動ガベージコレクションを実行しました。",
            collected_gc=collected,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="情報更新",
        style=discord.ButtonStyle.secondary,
        emoji="🔄",
        row=1,
    )
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await _build_admin_embed(self.cog.bot, last_action="🔄 情報を最新に更新しました。")
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        status_tracker.record_error(f"/admin view: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
            else:
                await interaction.response.send_message("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
        except (discord.HTTPException, discord.InteractionResponded):
            pass


class AdminMemberListView(discord.ui.View):
    """プレミアム会員一覧表示時のView（戻るボタン付き）"""

    def __init__(self, cog: "AdminCog", parent_view: AdminControlView):
        super().__init__(timeout=300)
        self.cog = cog
        self.parent_view = parent_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_owner(interaction):
            await interaction.response.send_message("❌ この操作はBot開発者専用です。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="戻る", style=discord.ButtonStyle.secondary, emoji="🔙")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await _build_admin_embed(self.cog.bot)
        await interaction.response.edit_message(embed=embed, view=self.parent_view)

    @discord.ui.button(label="リスト再読み込み", style=discord.ButtonStyle.primary, emoji="🔄")
    async def reload_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await _build_members_embed(self.cog.bot)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        status_tracker.record_error(f"/admin list view: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
            else:
                await interaction.response.send_message("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
        except (discord.HTTPException, discord.InteractionResponded):
            pass


# ==========================================
# Cog 本体
# ==========================================

class AdminCog(commands.Cog):
    """開発者専用の管理コマンドCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="admin",
        description="【開発者限定】管理コントロールパネルを開きます（プレミアム管理・診断・パネル設置）",
    )
    async def admin(self, interaction: discord.Interaction):
        """開発者専用の統合コントロールパネルを表示する"""
        if not is_owner(interaction):
            await interaction.response.send_message(
                "❌ このコマンドはBot開発者専用です。",
                ephemeral=True,
            )
            return

        embed = await _build_admin_embed(self.bot)
        view = AdminControlView(self)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
