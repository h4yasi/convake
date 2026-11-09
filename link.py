"""
画像・動画・テキストをDiscordのCDN直リンクに変換するコマンド。

仕組み: 指定した保管用チャンネル（config.LINK_STORAGE_CHANNEL_ID）にBotが投稿し、
そのメッセージに付いた添付ファイルのCDN URL（サーバーに入っていない人でも開ける）を返す。
外部の画像ホスティングサービスは使わず、Discordの仕組みだけで完結させている。

重要な注意点:
- DiscordのCDN URLは2023年12月以降「署名付き・期限付き」(ex/is/hmパラメータ)で、
  一定時間(目安: 約1日という情報があるが公式の正確な値は未確認)で開けなくなる。
  Discord自身が「CDNを恒久的なファイルホスティングに使わせないため」と説明している。
  このため /link が返すURLも期限切れになる前提で設計している（ユーザーにもその旨を案内する）。
- 期限切れ後も、保管用チャンネルのメッセージをAPIで取得し直せば新しい有効なURLが得られる。
  この仕組みを使い、履歴(/link_list)から常に新しいリンクを再発行して表示し、
  /link_delete で保管メッセージごと削除できるようにしている。
- 履歴は無料会員にも提供し、保存期間と保存件数でプレミアムと差をつけている
  （config.LINK_RETENTION_DAYS_* / LINK_HISTORY_MAX_*）。保存期間は作成した時点の
  会員種別で確定する。期限が過ぎた履歴は、定期掃除で「履歴の記録だけ」取り除かれ、
  以後は再発行できなくなる。保管用チャンネルのメッセージ自体は削除せず残している
  （管理者が内容を確認・管理しやすいように）。本人が /link_delete を実行したときだけは、
  本人の削除要求なので保管メッセージも削除する。
- 保管用チャンネルが任意のファイルをアップロードできる汎用ホスティングと化さないよう、
  mediaは画像・動画拡張子のみ、fileはテキスト系拡張子のみに絞っている。
- 動画もmediaパラメータで受け付ける。/linkはffmpeg等の変換処理を一切行わず
  「ファイルをそのまま保管チャンネルに転送してURLを返す」だけなので、画像でも動画でも
  処理コストは同じ（/convertの動画→GIF変換のようにプレミアム限定にする理由が無い）。
"""
import io
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    LINK_STORAGE_CHANNEL_ID,
    MAX_LINK_TEXT_CHARS,
    LINK_RETENTION_DAYS_FREE,
    LINK_RETENTION_DAYS_PREMIUM,
    LINK_HISTORY_MAX_FREE,
    LINK_HISTORY_MAX_PREMIUM,
)
from utils.file_helpers import (
    FileLimitError,
    validate_attachment_size,
    validate_data_size,
    is_image_ext,
    get_extension,
)
from utils.rate_limit import with_command_limits
from utils.ephemeral import get_ephemeral
from utils import premium_store, link_store

# fileパラメータで受け付けるテキスト系拡張子（実行ファイル等を受け付けないようにするため限定している）
TEXT_FILE_EXTENSIONS = {"txt", "md", "csv", "json", "log"}
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "webm", "avi", "m4v"}


def _is_media_ext(ext: str) -> bool:
    return is_image_ext(ext) or ext in VIDEO_EXTENSIONS


logger = logging.getLogger("discord_bot")

def format_days(days: int) -> str:
    """30日→「1ヶ月」、180日→「6ヶ月」のように、月で割り切れる場合は月表記にする。"""
    if days % 30 == 0:
        return f"{days // 30}ヶ月"
    return f"{days}日"


def retention_policy(is_premium: bool) -> Tuple[int, int]:
    """(保存日数, 保存件数上限) を会員種別ごとに返す。"""
    if is_premium:
        return LINK_RETENTION_DAYS_PREMIUM, LINK_HISTORY_MAX_PREMIUM
    return LINK_RETENTION_DAYS_FREE, LINK_HISTORY_MAX_FREE


class LinkCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self._sweep_expired.start()

    async def cog_unload(self):
        self._sweep_expired.cancel()

    @tasks.loop(hours=6)
    async def _sweep_expired(self):
        """保存期限が過ぎた履歴の「記録」だけを取り除く（保管メッセージは残す）。
        Discord APIを呼ばないので、レート制限の心配は無い。"""
        try:
            removed = await link_store.purge_expired(datetime.now(timezone.utc))
            if removed:
                logger.info("/link履歴: 保存期限が過ぎた%d件を履歴から取り除きました", removed)
        except Exception:
            # 例外でループが止まると以後掃除されなくなるため、握りつぶしてログに残す
            logger.exception("/link履歴の掃除に失敗しました")

    async def _get_storage_channel(self):
        """保管用チャンネルを取得する。未設定・取得失敗ならNone。"""
        if not LINK_STORAGE_CHANNEL_ID:
            return None
        channel = self.bot.get_channel(LINK_STORAGE_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(LINK_STORAGE_CHANNEL_ID)
            except discord.HTTPException:
                channel = None
        return channel

    @app_commands.command(
        name="link",
        description="画像・動画やテキストをリンク化します（media・text・fileのいずれか1つを指定）",
    )
    @with_command_limits
    @app_commands.describe(
        media="リンク化したい画像または動画",
        text=f"リンク化したい短いテキスト（.txtとして保存されます、{MAX_LINK_TEXT_CHARS}文字まで）",
        file="リンク化したいテキストファイル（txt/md/csv/json/log）",
    )
    async def link(
        self,
        interaction: discord.Interaction,
        media: Optional[discord.Attachment] = None,
        text: Optional[str] = None,
        file: Optional[discord.Attachment] = None,
    ):
        provided = [v for v in (media, text, file) if v is not None]
        if len(provided) == 0:
            await interaction.response.send_message(
                "media・text・fileのいずれか1つを指定してください。", ephemeral=True
            )
            return
        if len(provided) > 1:
            await interaction.response.send_message(
                "media・text・fileはどれか1つだけ指定してください。", ephemeral=True
            )
            return

        if not LINK_STORAGE_CHANNEL_ID:
            await interaction.response.send_message("この機能は現在ご利用いただけません。", ephemeral=True)
            return

        storage_channel = await self._get_storage_channel()
        if storage_channel is None or not hasattr(storage_channel, "send"):
            await interaction.response.send_message(
                "保管用チャンネルが見つかりませんでした。管理者に連絡してください。", ephemeral=True
            )
            return

        is_premium = await premium_store.is_active(interaction.user.id)
        retention_days, max_records = retention_policy(is_premium)
        # 履歴に残せない（上限到達）状態でアップロードすると、後から再発行・削除できない
        # 「孤立したメッセージ」が保管チャンネルに残ってしまうため、アップロード前に止める。
        if await link_store.is_full(interaction.user.id, max_records):
            await interaction.response.send_message(
                f"履歴が上限（{max_records}件）に達しています。`/link_delete` で不要なものを削除するか、"
                "保存期限が過ぎて履歴から外れるのをお待ちください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=get_ephemeral())

        try:
            if media is not None:
                ext = get_extension(media.filename)
                if not _is_media_ext(ext):
                    await interaction.followup.send("画像または動画ファイルを指定してください。")
                    return
                validate_attachment_size(media)
                data = await media.read()
                validate_data_size(data)
                discord_file = discord.File(io.BytesIO(data), filename=media.filename)
                kind = "media"

            elif file is not None:
                ext = get_extension(file.filename)
                if ext not in TEXT_FILE_EXTENSIONS:
                    await interaction.followup.send(
                        f"対応しているファイル形式は {', '.join(sorted(TEXT_FILE_EXTENSIONS))} のみです。"
                    )
                    return
                validate_attachment_size(file)
                data = await file.read()
                validate_data_size(data)
                discord_file = discord.File(io.BytesIO(data), filename=file.filename)
                kind = "file"

            else:
                if len(text) > MAX_LINK_TEXT_CHARS:
                    await interaction.followup.send(f"テキストが長すぎます（上限{MAX_LINK_TEXT_CHARS}文字）。")
                    return
                data = text.encode("utf-8")
                discord_file = discord.File(io.BytesIO(data), filename="text.txt")
                kind = "text"

        except FileLimitError as e:
            await interaction.followup.send(str(e))
            return

        try:
            sent = await storage_channel.send(
                content=f"📎 {interaction.user} からのリンク化リクエスト",
                file=discord_file,
            )
        except discord.HTTPException:
            await interaction.followup.send("リンクの生成に失敗しました。時間を置いて再試行してください。")
            return

        if not sent.attachments:
            await interaction.followup.send("リンクの生成に失敗しました。時間を置いて再試行してください。")
            return

        attachment = sent.attachments[0]
        recorded = await link_store.add(
            interaction.user.id, sent.id, attachment.filename, kind, retention_days, max_records
        )
        message = f"🔗 {attachment.url}\n※ このリンクは一定時間（目安: 約1日）で期限切れになります。"
        if recorded:
            message += (
                f"\n`/link_list` で新しいリンクを再発行できます"
                f"（履歴は{format_days(retention_days)}保存され、期限が来ると履歴から外れて再発行できなくなります）。"
            )
        if not is_premium:
            message += f"\nプレミアム会員は履歴を{format_days(LINK_RETENTION_DAYS_PREMIUM)}保存できます。"
        await interaction.followup.send(message, ephemeral=get_ephemeral())

    # --- 履歴一覧・再発行 ---

    @app_commands.command(
        name="link_list",
        description="作成したリンクの履歴を、新しい有効なリンクで再発行して表示します",
    )
    @with_command_limits
    async def link_list(self, interaction: discord.Interaction):
        storage_channel = await self._get_storage_channel()
        if storage_channel is None or not hasattr(storage_channel, "fetch_message"):
            await interaction.response.send_message("この機能は現在ご利用いただけません。", ephemeral=True)
            return

        records = await link_store.list_records(interaction.user.id)
        if not records:
            await interaction.response.send_message("履歴はまだありません。", ephemeral=True)
            return

        # URLを含むため、履歴は常に本人だけに見える形で返す
        await interaction.response.defer(thinking=True, ephemeral=True)

        lines: List[str] = []
        for i, rec in enumerate(records, start=1):
            try:
                created = link_store.parse_utc(rec["created_at"])
                when = discord.utils.format_dt(created, style="f")
            except (KeyError, ValueError, TypeError):
                when = "日時不明"
            name = discord.utils.escape_markdown(rec.get("filename", "?"))
            exp = link_store.expiry_of(rec)
            when += f"（{discord.utils.format_dt(exp, style='D')}まで再発行できます）" if exp else ""
            try:
                msg = await storage_channel.fetch_message(int(rec["message_id"]))
                # メッセージを取得し直すと、新しい有効期限の署名付きURLが返ってくる
                lines.append(f"**{i}.** {name} — {when}\n[リンクを開く]({msg.attachments[0].url})")
            except (discord.NotFound, IndexError):
                # 保管メッセージが既に消えている場合は、履歴からも取り除く
                await link_store.remove(interaction.user.id, rec["message_id"])
                lines.append(f"**{i}.** {name} — {when}\n（保管データが削除されていたため、履歴から取り除きました）")
            except discord.HTTPException:
                lines.append(f"**{i}.** {name} — {when}\n（取得に失敗しました。時間を置いて再試行してください）")

        # Discordのembed文字数上限(1つあたり4096、署名付きURLは長い)を超えないよう分割して送る
        chunks: List[str] = []
        current = ""
        for line in lines:
            if current and len(current) + len(line) + 2 > 3500:
                chunks.append(current)
                current = ""
            current += ("\n\n" if current else "") + line
        if current:
            chunks.append(current)

        for idx, chunk in enumerate(chunks):
            embed = discord.Embed(
                title="🔗 リンク履歴（新しいリンクで再発行済み）" if idx == 0 else None,
                description=chunk,
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    # --- 削除 ---

    @app_commands.command(
        name="link_delete",
        description="作成したリンクを保管データごと削除します",
    )
    @with_command_limits
    @app_commands.describe(link="削除するリンク（履歴から選択）")
    async def link_delete(self, interaction: discord.Interaction, link: str):
        try:
            message_id = int(link)
        except ValueError:
            await interaction.response.send_message("履歴の一覧から選択してください。", ephemeral=True)
            return

        # 必ず「本人の履歴に存在するメッセージID」だけを対象にする（他人のメッセージは削除できない）
        record = await link_store.get(interaction.user.id, message_id)
        if record is None:
            await interaction.response.send_message("該当する履歴が見つかりませんでした。", ephemeral=True)
            return

        storage_channel = await self._get_storage_channel()
        if storage_channel is None or not hasattr(storage_channel, "fetch_message"):
            await interaction.response.send_message("この機能は現在ご利用いただけません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            msg = await storage_channel.fetch_message(message_id)
            await msg.delete()
        except discord.NotFound:
            pass  # 既に消えている場合も、履歴から取り除けば目的は達成されている
        except discord.HTTPException:
            await interaction.followup.send("削除に失敗しました。時間を置いて再試行してください。", ephemeral=True)
            return

        await link_store.remove(interaction.user.id, message_id)
        await interaction.followup.send(
            f"🗑️ 「{discord.utils.escape_markdown(record.get('filename', '?'))}」を削除しました。"
            "このリンクは以後使えなくなります。",
            ephemeral=True,
        )

    @link_delete.autocomplete("link")
    async def link_delete_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = []
        for rec in await link_store.list_records(interaction.user.id):
            try:
                when = datetime.fromisoformat(rec["created_at"]).strftime("%m/%d %H:%M")
            except (KeyError, ValueError):
                when = "?"
            label = f"{rec.get('filename', '?')}（{when}）"
            if current and current.lower() not in label.lower():
                continue
            # 選択肢のvalueは文字列。メッセージIDは64bit整数で、数値型のオプションだと精度が足りないため
            choices.append(app_commands.Choice(name=label[:100], value=str(rec["message_id"])))
        return choices[:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(LinkCog(bot))
