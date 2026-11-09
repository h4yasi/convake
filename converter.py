"""
ファイル変換・画像圧縮のスラッシュコマンド
- /convert file:<添付> target:<選択肢>
- /compress file:<添付> quality:<1-95> max_dimension:<px>
"""
import gc
import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.file_helpers import (
    FileLimitError,
    get_extension,
    is_image_ext,
    validate_attachment_size,
    validate_data_size,
)
from utils.converters import (
    ConversionError,
    convert_image_to_image,
    convert_pdf_to_png,
    convert_image_to_pdf,
    convert_txt_to_pdf,
    convert_csv_to_xlsx,
    compress_image,
)
from utils.video import convert_video_to_gif, convert_gif_to_mp4, VideoConversionError
from utils.concurrency import run_cpu_bound, heavy_task_guard, PRIORITY_PREMIUM, PRIORITY_FREE
from utils.rate_limit import with_command_limits
from utils import premium_store
from utils.ephemeral import get_ephemeral
from config import PREMIUM_PROMOTION_MESSAGE

MAX_DISCORD_FILES_PER_MESSAGE = 10
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "webm", "avi", "m4v"}
logger = logging.getLogger("discord_bot")

# 表示名だけ日本語にし、内部で使う値（拡張子文字列、変換ロジック側が参照する）はそのまま。
CONVERT_TARGET_CHOICES = [
    app_commands.Choice(name="PNG画像", value="png"),
    app_commands.Choice(name="JPG画像", value="jpg"),
    app_commands.Choice(name="WebP画像", value="webp"),
    app_commands.Choice(name="BMP画像", value="bmp"),
    app_commands.Choice(name="TIFF画像", value="tiff"),
    app_commands.Choice(name="ICO", value="ico"),
    app_commands.Choice(name="PDF", value="pdf"),
    app_commands.Choice(name="Excel(xlsx)", value="xlsx"),
    app_commands.Choice(name="GIF(動画から・プレミアム限定)", value="gif"),
    app_commands.Choice(name="MP4動画(GIFから・プレミアム限定)", value="mp4"),
]


class ConverterCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _strip_ext(filename: str) -> str:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        stem = "".join(char if char.isprintable() and char not in "/\\" else "_" for char in stem)
        return stem[:100] or "converted"

    @app_commands.command(name="convert", description="ファイルを別の形式に変換します")
    @with_command_limits
    @app_commands.describe(
        file="変換したいファイル（画像 / PDF / TXT / CSV / 動画 / GIF）",
        target="変換先のフォーマットを選択してください（画像はGIFを除く全形式が相互変換可能。動画⇔GIFは別枠で対応）",
    )
    @app_commands.choices(target=CONVERT_TARGET_CHOICES)
    async def convert(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        target: app_commands.Choice[str],
    ):
        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        source_ext = get_extension(file.filename)
        target_ext = target.value

        # 動画⇔GIFはffmpegを使うぶん他の変換より重いため、プレミアム会員限定にしている。
        # 判定はロールではなくユーザーID自体で行うため、Botがどのサーバーにいても
        # (DMでも)同じ人なら使える。
        is_video_conversion = (source_ext in VIDEO_EXTENSIONS and target_ext == "gif") or (
            source_ext == "gif" and target_ext == "mp4"
        )
        is_premium = await premium_store.is_active(interaction.user.id)
        if is_video_conversion and not is_premium:
            embed = discord.Embed(
                description=f"動画⇔GIFの変換はプレミアム会員限定です。\n{PREMIUM_PROMOTION_MESSAGE}",
                color=discord.Color.gold(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=get_ephemeral())

        try:
            async with heavy_task_guard(PRIORITY_PREMIUM if is_premium else PRIORITY_FREE):
                data = await file.read()
                validate_data_size(data)

                # 画像 -> 画像
                if is_image_ext(source_ext) and is_image_ext(target_ext):
                    result = await run_cpu_bound(convert_image_to_image, data, target_ext)
                    filename = f"{self._strip_ext(file.filename)}.{target_ext}"
                    await interaction.followup.send(
                        content=f"✨ **{target_ext.upper()} に化けました！**",
                        file=discord.File(io.BytesIO(result), filename=filename),
                        ephemeral=get_ephemeral(),
                    )
                    return

                # PDF -> PNG
                if source_ext == "pdf" and target_ext == "png":
                    pages = await run_cpu_bound(convert_pdf_to_png, data)
                    if not pages:
                        await interaction.followup.send("PDFにページが見つかりませんでした。", ephemeral=get_ephemeral())
                        return
                    await interaction.followup.send(content="**PDFからPNG画像に化けたよ！**", ephemeral=get_ephemeral())
                    await self._send_pages(interaction, pages)
                    return

                # 画像 -> PDF
                if is_image_ext(source_ext) and target_ext == "pdf":
                    result = await run_cpu_bound(convert_image_to_pdf, data)
                    filename = f"{self._strip_ext(file.filename)}.pdf"
                    await interaction.followup.send(
                        content="**PDF文書に化けたよ！**",
                        file=discord.File(io.BytesIO(result), filename=filename),
                        ephemeral=get_ephemeral(),
                    )
                    return

                # TXT -> PDF
                if source_ext == "txt" and target_ext == "pdf":
                    result = await run_cpu_bound(convert_txt_to_pdf, data)
                    filename = f"{self._strip_ext(file.filename)}.pdf"
                    await interaction.followup.send(
                        content="**テキストからPDF文書に化けたよ！**",
                        file=discord.File(io.BytesIO(result), filename=filename),
                        ephemeral=get_ephemeral(),
                    )
                    return

                # CSV -> XLSX
                if source_ext == "csv" and target_ext == "xlsx":
                    result = await run_cpu_bound(convert_csv_to_xlsx, data)
                    filename = f"{self._strip_ext(file.filename)}.xlsx"
                    await interaction.followup.send(
                        content="**Excel(xlsx)シートに化けたよ！**",
                        file=discord.File(io.BytesIO(result), filename=filename),
                        ephemeral=get_ephemeral(),
                    )
                    return

                # 動画 -> GIF （ffmpeg呼び出しはブロッキングなので別スレッドで実行。
                # また、メモリ/CPU負荷が高いためセマフォで同時実行数を制限する）
                if source_ext in VIDEO_EXTENSIONS and target_ext == "gif":
                    result = await run_cpu_bound(convert_video_to_gif, data)
                    filename = f"{self._strip_ext(file.filename)}.gif"
                    await interaction.followup.send(
                        content="**GIFに化けたよ！**",
                        file=discord.File(io.BytesIO(result), filename=filename),
                        ephemeral=get_ephemeral(),
                    )
                    result = None
                    gc.collect()
                    return

                # GIF -> MP4
                if source_ext == "gif" and target_ext == "mp4":
                    result = await run_cpu_bound(convert_gif_to_mp4, data)
                    filename = f"{self._strip_ext(file.filename)}.mp4"
                    await interaction.followup.send(
                        content="**MP4動画に化けたよ！**",
                        file=discord.File(io.BytesIO(result), filename=filename),
                        ephemeral=get_ephemeral(),
                    )
                    result = None
                    gc.collect()
                    return

                await interaction.followup.send(
                    f"未対応の変換です: `{source_ext or '不明'}` -> `{target_ext}`\n"
                    "対応している変換: 画像⇔画像(GIFを除く全形式) / PDF→PNG / 画像→PDF / TXT→PDF / CSV→XLSX / 動画→GIF / GIF→MP4",
                    ephemeral=get_ephemeral(),
                )

        except ConversionError as e:
            await interaction.followup.send(f"変換エラー: {e}", ephemeral=get_ephemeral())
        except VideoConversionError as e:
            await interaction.followup.send(f"動画変換エラー: {e}", ephemeral=get_ephemeral())
        except FileLimitError as e:
            await interaction.followup.send(str(e), ephemeral=True)
        except Exception as e:
            logger.exception("convert command failed")
            await interaction.followup.send(
                "変換中に予期しないエラーが発生しました。入力ファイルを確認してください。",
                ephemeral=True,
            )
        finally:
            gc.collect()

    @app_commands.command(name="compress", description="画像を圧縮します")
    @with_command_limits
    @app_commands.describe(
        file="圧縮したい画像",
        quality="画質(1-95、デフォルト70)。数値が低いほど圧縮率が高くなります",
        max_dimension="長辺の最大ピクセル数（指定するとリサイズしてさらに軽量化）",
    )
    async def compress(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        quality: Optional[app_commands.Range[int, 1, 95]] = 70,
        max_dimension: Optional[app_commands.Range[int, 64, 4000]] = None,
    ):
        ext = get_extension(file.filename)
        if not is_image_ext(ext):
            await interaction.response.send_message("画像ファイルではないため圧縮できません。", ephemeral=True)
            return

        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        is_premium = await premium_store.is_active(interaction.user.id)
        await interaction.response.defer(thinking=True, ephemeral=get_ephemeral())
        try:
            async with heavy_task_guard(PRIORITY_PREMIUM if is_premium else PRIORITY_FREE):
                data = await file.read()
                validate_data_size(data)
                before_size = len(data)
                result, out_ext = await run_cpu_bound(
                    compress_image,
                    data,
                    quality=70 if quality is None else quality,
                    max_dimension=max_dimension,
                )
                after_size = len(result)
                filename = f"{self._strip_ext(file.filename)}_compressed.{out_ext}"

                percent = (1 - after_size / before_size) * 100 if before_size else 0
                await interaction.followup.send(
                    content=(
                        f"**軽量になった姿に化けたよ！** {before_size/1024:.1f}KB → {after_size/1024:.1f}KB "
                        f"({percent:.1f}% 削減)"
                    ),
                    file=discord.File(io.BytesIO(result), filename=filename),
                    ephemeral=get_ephemeral(),
                )
                data = None
                result = None
        except FileLimitError as e:
            await interaction.followup.send(str(e), ephemeral=True)
        except Exception:
            logger.exception("compress command failed")
            await interaction.followup.send(
                "圧縮中に予期しないエラーが発生しました。入力ファイルを確認してください。",
                ephemeral=True,
            )
        finally:
            gc.collect()

    @staticmethod
    async def _send_pages(interaction: discord.Interaction, pages):
        """PDF->PNG変換結果を、10枚ずつのメッセージに分けて送信する（Discordの1メッセージ添付上限対策）"""
        ephemeral = get_ephemeral()
        for i in range(0, len(pages), MAX_DISCORD_FILES_PER_MESSAGE):
            chunk = pages[i : i + MAX_DISCORD_FILES_PER_MESSAGE]
            files = [discord.File(io.BytesIO(data), filename=name) for name, data in chunk]
            try:
                await interaction.followup.send(files=files, ephemeral=ephemeral)
            except discord.HTTPException:
                if len(chunk) == 1:
                    raise
                for name, data in chunk:
                    await interaction.followup.send(
                        file=discord.File(io.BytesIO(data), filename=name),
                        ephemeral=ephemeral,
                    )


async def setup(bot: commands.Bot):
    await bot.add_cog(ConverterCog(bot))
