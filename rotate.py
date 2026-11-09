"""
画像の回転機能。

/rotate: 添付画像と回転角度(90/180/270)を指定して、回転結果をそのままファイルで返す。
埋め込み+ボタンによる追加調整UIは廃止し、他のコマンド（/convert, /compress等）と
同じ「コマンド1回で完結する」形に統一した。

回転は Pillow の transpose（90度単位の軸回転）を使うため、リサンプリングを伴わず
画質劣化が無く、処理も一瞬で終わる。動画変換などとは負荷が桁違いに軽いため、
MAX_CONCURRENT_HEAVY_TASKS のセマフォ（heavy_task_guard）は通さず、
素の別スレッド実行（asyncio.to_thread）だけで十分と判断している。

出力形式は、透明度が必要な画像だけPNG、それ以外はJPEGにしている
（ファイルサイズを抑えるため）。
"""
import asyncio
import gc
import io
import re

import discord
from PIL import Image, ImageOps
from discord import app_commands
from discord.ext import commands

from utils.file_helpers import (
    FileLimitError,
    validate_attachment_size,
    validate_data_size,
    is_image_ext,
    get_extension,
)
from utils.rate_limit import with_command_limits
from utils.ephemeral import get_ephemeral
from config import MAX_IMAGE_PIXELS


class RotateError(Exception):
    pass


def _safe_filename(name: str) -> str:
    """Discordの添付ファイル名として安全な文字列に変換する（非ASCII文字等を除去）。"""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_")
    return cleaned or "image"


def _rotate_sync(original: bytes, angle: int) -> tuple[bytes, str]:
    """同期処理本体。asyncio.to_threadから呼ばれる。戻り値は (画像バイト列, 拡張子)。"""
    try:
        img = Image.open(io.BytesIO(original))
    except Exception as exc:
        raise RotateError("画像ファイルを読み込めませんでした。") from exc

    try:
        # 重要: サイズチェックは必ずimg.load()より前に行うこと。
        # Image.open()はヘッダーだけを読む遅延処理で、実際のピクセルデコード(メモリ確保)は
        # load()で初めて発生する。ここでの順序を逆にすると、ヘッダー上の寸法だけ巨大で
        # 実データは小さい悪意ある画像（decompression bomb）を送られた際に、
        # 上限チェックより先にメモリを大量消費してBotをクラッシュさせられてしまう。
        if img.width < 1 or img.height < 1 or img.width * img.height > MAX_IMAGE_PIXELS:
            raise RotateError(f"画像が大きすぎます（上限{MAX_IMAGE_PIXELS / 1_000_000:.0f}MPixel）。")
        img.load()
        try:
            transposed = ImageOps.exif_transpose(img)
            if transposed is not None and transposed is not img:
                img.close()
                img = transposed
        except Exception:
            pass

        normalized_angle = angle % 360
        transpose_enum = getattr(Image, "Transpose", Image)
        if normalized_angle == 90:
            rotated = img.transpose(transpose_enum.ROTATE_270)
        elif normalized_angle == 180:
            rotated = img.transpose(transpose_enum.ROTATE_180)
        elif normalized_angle == 270:
            rotated = img.transpose(transpose_enum.ROTATE_90)
        else:
            rotated = img.copy()

        try:
            has_alpha = rotated.mode in ("RGBA", "LA") or (
                rotated.mode == "P" and "transparency" in rotated.info
            )
            buf = io.BytesIO()
            if has_alpha:
                rotated.convert("RGBA").save(buf, format="PNG")
                return buf.getvalue(), "png"
            rotated.convert("RGB").save(buf, format="JPEG", quality=92)
            return buf.getvalue(), "jpg"
        finally:
            rotated.close()
    except RotateError:
        raise
    except Exception as exc:
        raise RotateError("画像の回転に失敗しました。") from exc
    finally:
        img.close()


async def rotate_image(original: bytes, angle: int) -> tuple[bytes, str]:
    return await asyncio.to_thread(_rotate_sync, original, angle)


class RotateCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="rotate", description="画像を回転します")
    @with_command_limits
    @app_commands.describe(image="回転する画像", angle="回転角度")
    @app_commands.choices(
        angle=[
            app_commands.Choice(name="90° 右", value=90),
            app_commands.Choice(name="180°", value=180),
            app_commands.Choice(name="90° 左", value=-90),
        ]
    )
    async def rotate(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        angle: app_commands.Choice[int],
    ):
        try:
            validate_attachment_size(image)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        ext_in = get_extension(image.filename)
        if not is_image_ext(ext_in):
            await interaction.response.send_message("画像ファイルを指定してください。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=get_ephemeral())

        try:
            data = await image.read()
            validate_data_size(data)
            result, ext = await rotate_image(data, angle.value)
        except (FileLimitError, RotateError) as e:
            await interaction.followup.send(str(e), ephemeral=get_ephemeral())
            return
        finally:
            gc.collect()

        base_filename = _safe_filename(image.filename.rsplit(".", 1)[0])
        filename = f"{base_filename}_rotated.{ext}"
        await interaction.followup.send(
            content=f"**くるっと {angle.value % 360}° 回転した姿に化けたよ！**",
            file=discord.File(io.BytesIO(result), filename=filename),
            ephemeral=get_ephemeral(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RotateCog(bot))
