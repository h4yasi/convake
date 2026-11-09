"""
添付ファイルの安全性を手動でチェックする機能。
以前の自動監視(on_message)は廃止し、明示的に呼び出した時だけ動作する:

1. `/scan file:<添付>`
   これから共有しようとしているファイル、または手元にあるファイルを事前にチェックする。
2. 右クリック(スマホは長押し) → アプリ → 「添付ファイルを検査」
   すでにチャンネルに投稿されたメッセージの添付ファイルをチェックする（メッセージのコンテキストメニュー）。

どちらも判定結果をチャンネルに通知するだけで、メッセージの削除は一切行わない。
"""
from typing import Optional

import aiohttp
import logging
import discord
from discord import app_commands
from discord.ext import commands

from config import MAX_SCAN_TOTAL_BYTES
from utils.security import scan_attachment_bytes, ScanResult
from utils.file_helpers import FileLimitError, validate_attachment_size, validate_data_size
from utils.rate_limit import with_command_limits

MAX_ATTACHMENTS_PER_SCAN = 5  # 1回のコンテキストメニュー実行で調べる添付ファイル数の上限
logger = logging.getLogger("discord_bot")


def _safe_filename(filename: str) -> str:
    return filename.replace("`", "'").replace("\r", " ").replace("\n", " ")


def _build_result_embed(filename: str, result: ScanResult) -> discord.Embed:
    filename = _safe_filename(filename)
    if result.is_flagged:
        embed = discord.Embed(
            title="危険な可能性があるファイルを検出",
            description=f"`{filename}`",
            color=discord.Color.red(),
        )
    elif result.is_suspicious:
        embed = discord.Embed(
            title="不審な判定があるファイル",
            description=f"`{filename}`",
            color=discord.Color.orange(),
        )
    else:
        embed = discord.Embed(
            title="問題は見つかりませんでした",
            description=f"`{filename}`",
            color=discord.Color.green(),
        )

    embed.add_field(
        name="拡張子チェック",
        value=(
            f"危険な拡張子です (`.{result.extension}`)" if result.is_dangerous_extension else "問題ありません"
        ),
        inline=False,
    )

    if result.vt_checked:
        vt_value = (
            f"マルウェア検出: {result.vt_malicious_count}件 / 不審判定: {result.vt_suspicious_count}件"
        )
        embed.add_field(name="VirusTotal照会結果", value=vt_value, inline=False)
    elif result.vt_error:
        embed.add_field(name="VirusTotal照会", value=f"未実施 ({result.vt_error})", inline=False)
    else:
        embed.add_field(name="VirusTotal照会", value="未実施", inline=False)

    embed.set_footer(text=f"SHA256: {result.sha256[:16]}…")
    return embed


class SecurityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # VirusTotal照会用のセッションはコマンド呼び出しのたびに作り直すと
        # TCP/TLSハンドシェイクを毎回やり直すことになり非効率なため、
        # Cogのライフサイクルに合わせて1つだけ生成し使い回す(cog_load/cog_unload参照)。
        self._session: Optional[aiohttp.ClientSession] = None
        # コンテキストメニューコマンドはクラス内で直接定義できない制約があるため、
        # モジュールレベルで定義したものをここでツリーに登録する。
        self.ctx_menu = app_commands.ContextMenu(
            name="添付ファイルを検査",
            callback=self.scan_message_attachments_callback,
        )
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_load(self):
        self._session = aiohttp.ClientSession()

    async def cog_unload(self):
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)
        if self._session is not None:
            await self._session.close()
            self._session = None

    @app_commands.command(
        name="scan",
        description="ファイルの危険性をチェックします",
    )
    @with_command_limits
    @app_commands.describe(file="チェックしたいファイル")
    async def scan(self, interaction: discord.Interaction, file: discord.Attachment):
        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            data = await file.read()
            validate_data_size(data)
        except (discord.HTTPException, FileLimitError) as e:
            logger.warning("scan attachment download failed: %s", e)
            await interaction.followup.send("ファイルの読み込みに失敗しました。", ephemeral=True)
            return

        result = await scan_attachment_bytes(file.filename, data, session=self._session)

        await interaction.followup.send(embed=_build_result_embed(file.filename, result), ephemeral=True)

    @with_command_limits
    async def scan_message_attachments_callback(self, interaction: discord.Interaction, message: discord.Message):
        if not message.attachments:
            await interaction.response.send_message("このメッセージに添付ファイルはありません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        targets = message.attachments[:MAX_ATTACHMENTS_PER_SCAN]

        total_bytes = 0
        for att in targets:
            try:
                validate_attachment_size(att)
                data = await att.read()
                validate_data_size(data)
                total_bytes += len(data)
                if total_bytes > MAX_SCAN_TOTAL_BYTES:
                    await interaction.followup.send(
                        f"合計スキャンサイズが上限({MAX_SCAN_TOTAL_BYTES / 1024 / 1024:.0f}MB)を超えたため、"
                        "残りは中止しました。",
                        ephemeral=True,
                    )
                    break
            except discord.HTTPException:
                await interaction.followup.send(
                    f"`{att.filename}` の読み込みに失敗しました。",
                    ephemeral=True,
                )
                continue
            except FileLimitError as e:
                await interaction.followup.send(
                    f"`{att.filename}`: {e}",
                    ephemeral=True,
                )
                continue
            result = await scan_attachment_bytes(att.filename, data, session=self._session)
            await interaction.followup.send(
                embed=_build_result_embed(att.filename, result),
                ephemeral=True,
            )

        if len(message.attachments) > MAX_ATTACHMENTS_PER_SCAN:
            await interaction.followup.send(
                f"添付ファイルが{len(message.attachments)}件あるため、"
                f"先頭{MAX_ATTACHMENTS_PER_SCAN}件のみチェックしました。",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(SecurityCog(bot))
