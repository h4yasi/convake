"""
Discord Bot エントリーポイント（スラッシュコマンド中心、管理コマンドのみ!プレフィックス）。

必要な準備:
1. pip install -r requirements.txt
2. .env ファイルを作成し、DISCORD_TOKEN などを設定する（.env.example 参照）
3. python main.py で起動
4. 起動後、スラッシュコマンドがDiscordに反映されるまで
   （DEV_GUILD_ID未設定時のグローバル同期は）最大1時間ほどかかる場合があります。
   開発中は .env の DEV_GUILD_ID にテストサーバーのIDを設定すると即座に反映されます。

開発者限定コマンド（プレミアム付与・剥奪・一覧・購入パネル設置・Bot診断・メモリ解放）は
スラッシュコマンド `/admin` に集約されています（config.OWNER_IDのユーザーのみ操作可能）。
埋め込みとインタラクティブボタン・モーダルにより、ワンストップで管理できます。
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    DISCORD_TOKEN,
    DEV_GUILD_ID,
    TEMP_DIR,
    MAX_MESSAGE_CACHE,
    OWNER_ID,
    BOT_DISPLAY_NAME,
    BOT_NAME_JA,
    SAVE_REACTION_EMOJI,
)
from utils.file_helpers import ensure_temp_dir
from utils import status_tracker, command_registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discord_bot")

# pyvips(libvips)は内部の処理詳細をINFOレベルで大量に出力するため、
# 通常運用では邪魔になる。WARNING以上のみ出すよう抑制する。
logging.getLogger("pyvips").setLevel(logging.WARNING)

INITIAL_COGS = [
    "cogs.converter",
    "cogs.merge",
    "cogs.rotate",
    "cogs.image_tools",
    "cogs.link",
    "cogs.reaction_save",
    "cogs.security",
    "cogs.help",
    "cogs.premium",
    "cogs.admin",
]


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    # サーバー参加検知(on_guild_join)に使用（特権インテント不要・defaultで有効）
    intents.guilds = True
    # メッセージ内容(リアクション保存機能でのプレビュー表示等)に使用
    intents.message_content = True
    # リアクション検知に必要
    intents.reactions = True
    # メモリ対策: メンバーインテントは意図的に有効化しない。
    # スラッシュコマンドの interaction.user には呼び出したメンバーの権限情報が
    # インテントに関係なく含まれているため、権限チェックのために全メンバーを
    # キャッシュ(chunk)する必要がない。大規模サーバーではこのキャッシュだけで
    # 数十〜数百MBのメモリを消費することがあるため無効のままにする。
    return intents


def _find_welcome_channel(
    guild: discord.Guild,
    bot_user: Optional[discord.ClientUser],
) -> Optional[discord.TextChannel]:
    """サーバー追加時に挨拶メッセージを送信できる最適なテキストチャンネルを探す。"""
    me = guild.me
    if me is None and bot_user is not None:
        me = guild.get_member(bot_user.id)
    perm_target = me if me is not None else getattr(guild, "default_role", None)
    if perm_target is None:
        return None

    def _can_send(ch: object) -> bool:
        if not isinstance(ch, discord.TextChannel):
            return False
        perms = ch.permissions_for(perm_target)
        return bool(perms.view_channel and perms.send_messages)

    # 1. サーバー設定の「システムメッセージチャンネル」が送信可能なら最優先
    if guild.system_channel is not None and _can_send(guild.system_channel):
        return guild.system_channel

    # 2. ルールチャンネル・コミュニティ更新チャンネル以外のテキストチャンネルから探す
    excluded_ids = {
        ch.id
        for ch in (guild.rules_channel, guild.public_updates_channel)
        if ch is not None
    }
    candidates = [
        ch
        for ch in guild.text_channels
        if ch.id not in excluded_ids and _can_send(ch)
    ]
    if not candidates:
        candidates = [ch for ch in guild.text_channels if _can_send(ch)]

    if not candidates:
        return None

    # Embed送信権限があり、一般的な雑談・案内向けチャンネル名があれば優先
    preferred_names = ("general", "一般", "雑談", "welcome", "ようこそ", "bot", "コマンド")
    for ch in candidates:
        if ch.permissions_for(perm_target).embed_links and any(
            keyword in ch.name.lower() for keyword in preferred_names
        ):
            return ch

    # 次にEmbed送信権限がある最上位テキストチャンネル
    for ch in candidates:
        if ch.permissions_for(perm_target).embed_links:
            return ch

    # Embed権限が無い場合はテキスト送信可能な最上位テキストチャンネル
    return candidates[0]


class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._welcomed_guild_ids: set[int] = set()

    async def setup_hook(self):
        ensure_temp_dir(TEMP_DIR)
        for ext in INITIAL_COGS:
            try:
                await self.load_extension(ext)
                logger.info("Loaded extension: %s", ext)
            except Exception as exc:
                logger.exception("Failed to load extension: %s", ext)
                status_tracker.record_error(f"load:{ext}: {exc}")

        # スラッシュコマンドをDiscordに同期する
        if DEV_GUILD_ID:
            guild = discord.Object(id=DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %d commands to guild %s (即時反映)", len(synced), DEV_GUILD_ID)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %d commands globally (反映まで最大1時間程度)", len(synced))

        # /help でタップ1つでコマンドを実行できるメンション（</name:id>）を組み立てるため、
        # 同期直後にしか手に入らないコマンドIDを保存しておく
        command_registry.set_command_ids({cmd.name: cmd.id for cmd in synced})

    async def on_ready(self):
        logger.info("Logged in as %s (id: %s) - %s 起動完了", self.user, self.user.id, BOT_DISPLAY_NAME)
        status_text = "画像をサクッと化けさせる相棒 | /help"
        try:
            try:
                activity = discord.CustomActivity(name=status_text, state=status_text)
            except TypeError:
                activity = discord.CustomActivity(name=status_text)
            await self.change_presence(activity=activity)
        except Exception:
            pass

    async def on_guild_join(self, guild: discord.Guild):
        """Botが新しいサーバーに追加されたとき、自動で紹介・挨拶メッセージを送信する。"""
        logger.info("Joined guild: %s (id: %s)", guild.name, guild.id)

        if guild.id in self._welcomed_guild_ids:
            return

        # ゲートウェイ再送等で既存サーバーに対して発火した場合の誤送信を防止
        if guild.me and guild.me.joined_at:
            joined_at = guild.me.joined_at
            if joined_at.tzinfo is None:
                joined_at = joined_at.replace(tzinfo=timezone.utc)
            age_seconds = (datetime.now(timezone.utc) - joined_at).total_seconds()
            if age_seconds > 300:
                return

        self._welcomed_guild_ids.add(guild.id)
        if len(self._welcomed_guild_ids) > 1_000:
            self._welcomed_guild_ids = set(list(self._welcomed_guild_ids)[-500:])

        channel = _find_welcome_channel(guild, self.user)
        if channel is None:
            logger.info("No sendable channel found in joined guild: %s (id: %s)", guild.name, guild.id)
            return

        me = guild.me or (guild.get_member(self.user.id) if self.user is not None else None)
        perm_target = me if me is not None else getattr(guild, "default_role", None)
        can_embed = bool(perm_target is not None and channel.permissions_for(perm_target).embed_links)
        help_mention = command_registry.mention("help")

        greeting_line = (
            f"はじめまして！画像や動画を変幻自在に変換し化けさせるbot、"
            f"**{BOT_DISPLAY_NAME}({BOT_NAME_JA})**です！"
        )
        plain_welcome = (
            f"{greeting_line}\n"
            "画像や動画の変換（/convert）、圧縮（/compress）、結合（/merge）、反転（/flip）、"
            "透かし入れ（/watermark）、絵文字・スタンプ化（/emoji, /sticker）、Exif削除（/strip_exif）などが使えます。\n"
            "詳しい使い方は `/help` を実行してみてください！"
        )

        try:
            if can_embed:
                embed = discord.Embed(
                    title=f"🦊 {BOT_DISPLAY_NAME}({BOT_NAME_JA}) がやってきました！",
                    description=(
                        f"{greeting_line}\n\n"
                        f"別のアプリやブラウザを開かなくても、Discordのチャット上で画像・動画・ファイルを"
                        f"サクッと変換＆加工できます。\n"
                        f"まずは {help_mention} で機能別のコマンド一覧をご覧ください！"
                    ),
                    color=discord.Color.gold(),
                )
                embed.add_field(
                    name="✨ できること（主な機能）",
                    value=(
                        f"🔄 **変換・軽量化・結合・回転**: {command_registry.mention('convert')} ・ "
                        f"{command_registry.mention('compress')} ・ {command_registry.mention('merge')} ・ "
                        f"{command_registry.mention('rotate')}\n"
                        f"🎨 **反転・透かし・絵文字・配色**: {command_registry.mention('flip')} ・ "
                        f"{command_registry.mention('watermark')} ・ {command_registry.mention('emoji')} ・ "
                        f"{command_registry.mention('sticker')} ・ {command_registry.mention('avatar_crop')} ・ "
                        f"{command_registry.mention('palette')}\n"
                        f"🛡️ **個人情報削除・リンク化・安全検査**: {command_registry.mention('strip_exif')} ・ "
                        f"{command_registry.mention('link')} ・ {command_registry.mention('scan')}"
                    ),
                    inline=False,
                )
                embed.add_field(
                    name="🖱️ 右クリック＆リアクションでも便利",
                    value=(
                        "・メッセージを**右クリック（スマホは長押し）→ アプリ** から左右反転・絵文字化・Exif削除・安全検査を即実行！\n"
                        f"・メッセージに {SAVE_REACTION_EMOJI} リアクションを付けると、自分のDMに保存リンクが届きます。"
                    ),
                    inline=False,
                )
                avatar = getattr(self.user, "display_avatar", None) if self.user is not None else None
                if avatar is not None:
                    embed.set_thumbnail(url=avatar.url)
                embed.set_footer(text=f"{BOT_DISPLAY_NAME}({BOT_NAME_JA}) • /help でページ別ヘルプを表示")
                try:
                    await channel.send(embed=embed)
                except discord.Forbidden:
                    await channel.send(plain_welcome)
            else:
                await channel.send(plain_welcome)
            logger.info("Sent welcome message to #%s in guild %s (id: %s)", channel.name, guild.name, guild.id)
        except discord.Forbidden:
            logger.warning("Missing permission to send welcome message in guild %s (id: %s)", guild.name, guild.id)
        except discord.HTTPException as e:
            logger.warning("Failed to send welcome message in guild %s (id: %s): %s", guild.name, guild.id, e)

    async def on_guild_remove(self, guild: discord.Guild):
        self._welcomed_guild_ids.discard(guild.id)


# メモリ対策のためのBot初期化オプション:
# - max_messages=None: このBotはどのコマンドもメッセージキャッシュに依存していないため、
#   デフォルトで保持される最大1000件のメッセージキャッシュを無効化する。
# - chunk_guilds_at_startup=False: メンバーインテントを使わないため起動時の全メンバー取得も行わない
#   （デフォルトでもFalseになるが、明示しておく）。
# - member_cache_flags=MemberCacheFlags.none(): メンバーキャッシュを一切保持しない。
def create_bot() -> MyBot:
    """
    Botインスタンスを新規作成する。

    重要: discord.pyのClient/Botは一度close()すると内部のaiohttpセッションが
    閉じられ、同じインスタンスを再度start()することはできない
    （"RuntimeError: Session is closed" の原因）。
    そのため、接続エラー等でリトライする際は必ずこの関数で
    新しいインスタンスを作り直す必要がある。
    """
    new_bot = MyBot(
        command_prefix="!",
        intents=build_intents(),
        max_messages=MAX_MESSAGE_CACHE,
        chunk_guilds_at_startup=False,
        member_cache_flags=discord.MemberCacheFlags.none(),
        # デフォルトの「Botへのメンションだけでもヘルプが出る」機能はOFF。
        # 管理コマンド(!premium_grant等)は一般ユーザーに存在を気付かせたくないため、
        # 組み込みのhelpコマンドも使わない。
        help_command=None,
    )
    # !コマンド(premium_grant等)のオーナー限定チェックはconfig.OWNER_IDで行うため、
    # commands.is_owner()等ではなくbot.owner_idとしてもここで設定しておく
    new_bot.owner_id = OWNER_ID

    @new_bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            message = "このコマンドを実行する権限がありません。"
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"クールダウン中です。{error.retry_after:.1f}秒後に再試行してください。"
        else:
            logger.exception("Unhandled app command error", exc_info=error)
            status_tracker.record_error(f"/{interaction.command.name if interaction.command else '?'}: {error}")
            message = "処理中にエラーが発生しました。時間を置いて再試行してください。"

        if interaction.type == discord.InteractionType.autocomplete:
            return

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except (discord.HTTPException, discord.InteractionResponded):
            pass

    @new_bot.event
    async def on_error(event_method, *args, **kwargs):
        # スラッシュコマンド以外（イベントハンドラ内など）で起きた未処理例外もステータスに記録する
        logger.exception("Unhandled error in event %s", event_method)
        status_tracker.record_error(f"event:{event_method}")

    @new_bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError):
        # !コマンド(premium_grant等)のエラーハンドラ。
        # CommandNotFound: "!"で始まる普通のチャットメッセージ全部で発生しうるので無視。
        # CheckFailure: オーナー以外が管理コマンドを叩いた場合。管理コマンドの存在を
        #   気付かせたくないため、あえて何も応答せず静かに無視する。
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return
        logger.exception("Unhandled prefix command error", exc_info=error)
        status_tracker.record_error(f"!{ctx.command}: {error}")

    return new_bot


def main():
    """
    ログイン失敗時（特に429レート制限）に即座にリトライすると、
    ホスティング側の自動再起動と組み合わさって「即クラッシュ→即再起動」のループになり、
    Discordのレート制限がさらに悪化・長期化する恐れがある。
    そのため、失敗時は指数バックオフで待機してからリトライする。
    """
    asyncio.run(_run_with_backoff())


async def _run_with_backoff():
    backoff_seconds = 30
    max_backoff_seconds = 15 * 60  # 最大15分待機

    while True:
        # 重要: リトライのたびに必ず新しいBotインスタンスを作成する。
        # 一度でも close された（= async with を抜けた）インスタンスは
        # 内部のaiohttpセッションが閉じられており再利用できないため、
        # 使い回すと "RuntimeError: Session is closed" になる。
        bot = create_bot()
        try:
            # `async with bot:` を使うことで、start()が例外で終了した場合でも
            # aiohttpの接続などを確実にクローズする
            # （"Unclosed connector" 警告の原因対策）
            async with bot:
                await bot.start(DISCORD_TOKEN)
            break  # 正常終了（close()が呼ばれた等）した場合はループを抜ける

        except discord.HTTPException as e:
            if e.status == 429:
                logger.error(
                    "Discordのグローバルレート制限に達しました。"
                    "同じトークンで複数プロセスが起動していないか、"
                    "短時間の再起動を繰り返していないか確認してください。"
                    "%d秒待機してから再試行します…",
                    backoff_seconds,
                )
            else:
                logger.exception("Discord APIエラーが発生しました。%d秒待機してから再試行します…", backoff_seconds)

        except discord.LoginFailure:
            # トークンが無効な場合はリトライしても無駄なので即座に終了する
            logger.error("ログインに失敗しました。DISCORD_TOKENが正しいか確認してください（リトライしません）。")
            return

        except Exception:
            logger.exception("予期しないエラーで停止しました。%d秒待機してから再試行します…", backoff_seconds)

        await asyncio.sleep(backoff_seconds)
        backoff_seconds = min(backoff_seconds * 2, max_backoff_seconds)


if __name__ == "__main__":
    main()
