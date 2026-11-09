"""
Discord向け画像加工・ユーティリティCog。
- /emoji: 絵文字用に最適化（128x128, 128KB以下）
- /sticker: スタンプ用に最適化（320x320, 512KB以下）
- /avatar_crop: アイコン用円形切り抜き（透過PNG）
- /strip_exif: Exif/GPS位置情報の完全抹消
- /palette: カラーパレット抽出 & HEXコード一覧
- 右クリック「絵文字用に変換」「Exif情報を削除」（コンテキストメニュー）
"""
import asyncio
import gc
import io
import logging
from typing import Literal, Optional

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
from config import BOT_NAME, BOT_NAME_JA, BOT_DISPLAY_NAME
from utils.rate_limit import with_command_limits
from utils.ephemeral import get_ephemeral
from utils.image_tools import (
    process_emoji,
    process_sticker,
    process_avatar_crop,
    process_strip_exif,
    process_palette,
    process_flip,
    process_watermark,
)

logger = logging.getLogger("discord_bot")


class ImageToolsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # コンテキストメニュー（右クリックから直接実行）
        self.ctx_emoji = app_commands.ContextMenu(
            name="絵文字用に変換",
            callback=self.context_emoji,
        )
        self.ctx_exif = app_commands.ContextMenu(
            name="Exif情報を削除",
            callback=self.context_strip_exif,
        )
        self.ctx_flip = app_commands.ContextMenu(
            name="画像を左右反転",
            callback=self.context_flip,
        )
        self.bot.tree.add_command(self.ctx_emoji)
        self.bot.tree.add_command(self.ctx_exif)
        self.bot.tree.add_command(self.ctx_flip)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.ctx_emoji.name, type=self.ctx_emoji.type)
        self.bot.tree.remove_command(self.ctx_exif.name, type=self.ctx_exif.type)
        self.bot.tree.remove_command(self.ctx_flip.name, type=self.ctx_flip.type)

    # ==========================================
    # 1. 絵文字化 (/emoji)
    # ==========================================
    @app_commands.command(
        name="emoji",
        description="画像をDiscord絵文字用に最適化します",
    )
    @with_command_limits
    @app_commands.describe(
        file="絵文字にしたい画像",
        fit="crop: 中央切り抜き / pad: 透明余白を追加",
    )
    async def emoji(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        fit: Optional[Literal["crop", "pad"]] = "crop",
    ):
        ext = get_extension(file.filename)
        if not is_image_ext(ext):
            await interaction.response.send_message(
                "画像ファイル（PNG, JPG, WebP, BMPなど）を指定してください。", ephemeral=True
            )
            return

        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        try:
            raw_data = await file.read()
            validate_data_size(raw_data)
            out_bytes = await asyncio.to_thread(process_emoji, raw_data, fit or "crop")
        except Exception as e:
            logger.exception("Emoji conversion failed")
            await interaction.followup.send(f"変換処理に失敗しました: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        size_kb = len(out_bytes) / 1024
        discord_file = discord.File(io.BytesIO(out_bytes), filename="emoji.png")

        embed = discord.Embed(
            title="絵文字用に化けたよ！",
            description=(
                f"**規格**: `128x128 px` • `{size_kb:.1f} KB` (Discord上限: 128KB)\n\n"
                "**保存方法**:\n"
                "・**スマホ**: 画像をタップして長押し ➔画像を保存\n"
                "・**PC**: 画像を右クリック ➔画像を保存\n\n"
               
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url="attachment://emoji.png")
        embed.set_footer(text=f"{BOT_NAME_JA} • 画像をタップ長押し／右クリックで保存できます")
        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)

    # ==========================================
    # 2. スタンプ化 (/sticker)
    # ==========================================
    @app_commands.command(
        name="sticker",
        description="画像をDiscordスタンプ用に最適化します",
    )
    @with_command_limits
    @app_commands.describe(
        file="スタンプにしたい画像",
        fit="crop: 中央切り抜き / pad: 透明余白を追加",
    )
    async def sticker(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        fit: Optional[Literal["crop", "pad"]] = "crop",
    ):
        ext = get_extension(file.filename)
        if not is_image_ext(ext):
            await interaction.response.send_message(
                "画像ファイルを指定してください。", ephemeral=True
            )
            return

        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        try:
            raw_data = await file.read()
            validate_data_size(raw_data)
            out_bytes = await asyncio.to_thread(process_sticker, raw_data, fit or "crop")
        except Exception as e:
            logger.exception("Sticker conversion failed")
            await interaction.followup.send(f"変換処理に失敗しました: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        size_kb = len(out_bytes) / 1024
        discord_file = discord.File(io.BytesIO(out_bytes), filename="sticker.png")

        embed = discord.Embed(
            title="スタンプ用に化けたよ！",
            description=(
                f"**規格**: `320x320 px` • `{size_kb:.1f} KB` (Discord上限: 512KB以内)\n\n"
                "**保存方法**:\n"
                "・**スマホ**: 画像をタップして長押し ➔画像を保存\n"
                "・**PC**: 画像を右クリック ➔画像を保存\n\n"
                
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url="attachment://sticker.png")
        embed.set_footer(text=f"{BOT_NAME_JA} • 画像をタップ長押し／右クリックで保存できます")
        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)

    # ==========================================
    # 3. アバター丸型切り抜き (/avatar_crop)
    # ==========================================
    @app_commands.command(
        name="avatar_crop",
        description="画像を円形で切り抜きます",
    )
    @with_command_limits
    @app_commands.describe(file="円形で切り抜きたい画像")
    async def avatar_crop(self, interaction: discord.Interaction, file: discord.Attachment):
        ext = get_extension(file.filename)
        if not is_image_ext(ext):
            await interaction.response.send_message(
                "画像ファイルを指定してください。", ephemeral=True
            )
            return

        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        try:
            raw_data = await file.read()
            validate_data_size(raw_data)
            out_bytes = await asyncio.to_thread(process_avatar_crop, raw_data)
        except Exception as e:
            logger.exception("Avatar crop failed")
            await interaction.followup.send(f"切り抜きに失敗しました: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        discord_file = discord.File(io.BytesIO(out_bytes), filename="avatar.png")
        embed = discord.Embed(
            title="まあるい姿に化けたよ！",
            description=(
                "アイコンにした際の参考や、アイコン用に使えます。\n\n"
                "**保存方法**:\n"
                "・**スマホ**: 画像をタップして長押し ➔画像を保存\n"
                "・**PC**: 画像を右クリック ➔画像を保存\n\n"
                
            ),
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url="attachment://avatar.png")
        embed.set_footer(text=f"{BOT_NAME_JA} • 画像をタップ長押し／右クリックで保存できます")
        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)

    # ==========================================
    # 4. Exif情報抹消 (/strip_exif)
    # ==========================================
    @app_commands.command(
        name="strip_exif",
        description="画像からGPS位置情報や撮影機材・日時などのExifメタデータを削除します",
    )
    @with_command_limits
    @app_commands.describe(file="メタデータを消去したい画像")
    async def strip_exif(self, interaction: discord.Interaction, file: discord.Attachment):
        ext = get_extension(file.filename)
        if not is_image_ext(ext):
            await interaction.response.send_message(
                "画像ファイルを指定してください。", ephemeral=True
            )
            return

        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        try:
            raw_data = await file.read()
            validate_data_size(raw_data)
            out_bytes, out_ext = await asyncio.to_thread(process_strip_exif, raw_data, ext)
        except Exception as e:
            logger.exception("Exif stripping failed")
            await interaction.followup.send(f"メタデータ消去に失敗しました: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        out_name = f"clean_{file.filename.rsplit('.', 1)[0]}.{out_ext}"
        discord_file = discord.File(io.BytesIO(out_bytes), filename=out_name)

        embed = discord.Embed(
            title="個人情報を消して真っ白に化けたよ！",
            description=(
                "GPS位置情報（緯度・経度）、撮影日時、カメラ端末名、レンズ情報などを削除しました。\n"
                
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"{BOT_NAME_JA} • Exifメタデータを完全抹消")
        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)

    # ==========================================
    # 5. カラーパレット抽出 (/palette)
    # ==========================================
    @app_commands.command(
        name="palette",
        description="画像から主要カラーを抽出します",
    )
    @with_command_limits
    @app_commands.describe(
        file="カラーパレットを調べたい画像",
        colors="抽出する色数（3〜8色、省略時6色）",
    )
    async def palette(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        colors: Optional[app_commands.Range[int, 3, 8]] = 6,
    ):
        ext = get_extension(file.filename)
        if not is_image_ext(ext):
            await interaction.response.send_message(
                "画像ファイルを指定してください。", ephemeral=True
            )
            return

        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        try:
            raw_data = await file.read()
            validate_data_size(raw_data)
            card_bytes, hex_codes = await asyncio.to_thread(process_palette, raw_data, colors or 6)
        except Exception as e:
            logger.exception("Palette extraction failed")
            await interaction.followup.send(f"パレット抽出に失敗しました: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        discord_file = discord.File(io.BytesIO(card_bytes), filename="palette.png")

        hex_text = "  ".join([f"`{c}`" for c in hex_codes])
        embed = discord.Embed(
            title="綺麗なカラーパレットに化けたよ！",
            description=f"画像から抽出された代表色 ({len(hex_codes)}色):\n\n{hex_text}",
            color=discord.Color.blurple(),
        )
        embed.set_image(url="attachment://palette.png")
        embed.set_footer(text=f"{BOT_NAME_JA} • デザイン・イラスト配色参考にご活用ください")

        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)

    # ==========================================
    # 6. 画像反転 (/flip)
    # ==========================================
    @app_commands.command(
        name="flip",
        description="画像を反転します",
    )
    @with_command_limits
    @app_commands.describe(
        file="反転したい画像",
        direction="反転方向（horizontal: 左右反転 / vertical: 上下反転）",
    )
    async def flip(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        direction: Optional[Literal["horizontal", "vertical"]] = "horizontal",
    ):
        ext = get_extension(file.filename)
        if not is_image_ext(ext):
            await interaction.response.send_message(
                "画像ファイルを指定してください。", ephemeral=True
            )
            return

        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        try:
            raw_data = await file.read()
            validate_data_size(raw_data)
            out_bytes, out_ext = await asyncio.to_thread(process_flip, raw_data, direction or "horizontal", ext)
        except Exception as e:
            logger.exception("Flip failed")
            await interaction.followup.send(f"反転処理に失敗しました: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        dir_label = "左右反転" if direction != "vertical" else "上下反転"
        out_name = f"flipped_{file.filename.rsplit('.', 1)[0]}.{out_ext}"
        discord_file = discord.File(io.BytesIO(out_bytes), filename=out_name)

        embed = discord.Embed(
            title="くるっと反転した姿に化けたよ！",
            description=(
                f"**反転方向**: `{dir_label}`\n\n"
                "**保存方法**:\n"
                "・**スマホ**: 画像をタップして長押し ➔画像を保存\n"
                "・**PC**: 画像を右クリック ➔画像を保存"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{BOT_NAME_JA} • 画像をタップ長押し／右クリックで保存できます")
        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)

    # ==========================================
    # 7. ウォーターマーク透かし入れ (/watermark)
    # ==========================================
    @app_commands.command(
        name="watermark",
        description="画像に文字透かしを合成します",
    )
    @with_command_limits
    @app_commands.describe(
        file="透かしを入れたい画像",
        text="入れたい文字",
        position="透かしの位置（bottom_right: 右下 / center: 中央斜め / repeat: 全面パターン）",
        opacity="透明度（medium: 50% / light: 25% / strong: 75%）",
    )
    async def watermark(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        text: str,
        position: Optional[Literal["bottom_right", "center", "repeat"]] = "bottom_right",
        opacity: Optional[Literal["light", "medium", "strong"]] = "medium",
    ):
        ext = get_extension(file.filename)
        if not is_image_ext(ext):
            await interaction.response.send_message(
                "画像ファイルを指定してください。", ephemeral=True
            )
            return

        try:
            validate_attachment_size(file)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        try:
            raw_data = await file.read()
            validate_data_size(raw_data)
            out_bytes, out_ext = await asyncio.to_thread(
                process_watermark, raw_data, text, position or "bottom_right", opacity or "medium", ext
            )
        except Exception as e:
            logger.exception("Watermark failed")
            await interaction.followup.send(f"透かし合成に失敗しました: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        pos_labels = {
            "bottom_right": "右下サイン",
            "center": "中央",
            "repeat": "全面パターン",
        }
        pos_label = pos_labels.get(position or "bottom_right", "右下サイン")

        out_name = f"watermarked_{file.filename.rsplit('.', 1)[0]}.{out_ext}"
        discord_file = discord.File(io.BytesIO(out_bytes), filename=out_name)

        embed = discord.Embed(
            title="透かし入りの姿に化けたよ！",
            description=(
                f"**透かし文字**: `{text}`\n"
                f"**配置タイプ**: `{pos_label}`\n\n"
                "**保存方法**:\n"
                "・**スマホ**: 画像をタップして長押し ➔画像を保存\n"
                "・**PC**: 画像を右クリック ➔画像を保存"
            ),
            color=discord.Color.teal(),
        )
        embed.set_footer(text=f"{BOT_NAME_JA} • 画像をタップ長押し／右クリックで保存できます")
        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)

    # ==========================================
    # 8. コンテキストメニュー（右クリック）
    # ==========================================
    @with_command_limits
    async def context_emoji(self, interaction: discord.Interaction, message: discord.Message):
        if not message.attachments:
            await interaction.response.send_message("このメッセージに画像が添付されていません。", ephemeral=True)
            return

        target = next((att for att in message.attachments if is_image_ext(get_extension(att.filename))), None)
        if not target:
            await interaction.response.send_message("画像ファイルが見つかりませんでした。", ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        try:
            raw_data = await target.read()
            validate_data_size(raw_data)
            out_bytes = await asyncio.to_thread(process_emoji, raw_data, "crop")
        except Exception as e:
            await interaction.followup.send(f"変換失敗: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        discord_file = discord.File(io.BytesIO(out_bytes), filename="emoji.png")
        embed = discord.Embed(
            title="絵文字用に化けたよ！",
            description=(
                f"**規格**: `128x128 px` • `{len(out_bytes) / 1024:.1f} KB`\n\n"
                " **保存方法**:\n"
                "・**スマホ**: 画像をタップして長押し ➔画像を保存\n"
                "・**PC**: 画像を右クリック ➔画像を保存\n\n"
               
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url="attachment://emoji.png")
        embed.set_footer(text=f"{BOT_NAME_JA} • 画像をタップ長押し／右クリックで保存できます")
        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)

    @with_command_limits
    async def context_strip_exif(self, interaction: discord.Interaction, message: discord.Message):
        if not message.attachments:
            await interaction.response.send_message("このメッセージに画像が添付されていません。", ephemeral=True)
            return

        target = next((att for att in message.attachments if is_image_ext(get_extension(att.filename))), None)
        if not target:
            await interaction.response.send_message("画像ファイルが見つかりませんでした。", ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        ext = get_extension(target.filename)
        try:
            raw_data = await target.read()
            validate_data_size(raw_data)
            out_bytes, out_ext = await asyncio.to_thread(process_strip_exif, raw_data, ext)
        except Exception as e:
            await interaction.followup.send(f"Exif削除失敗: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        out_name = f"clean_{target.filename.rsplit('.', 1)[0]}.{out_ext}"
        discord_file = discord.File(io.BytesIO(out_bytes), filename=out_name)
        embed = discord.Embed(
            title="個人情報を消して真っ白に化けたよ！",
            description="GPS位置情報・撮影日時などを削除しました。",
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"{BOT_NAME_JA} • Exifメタデータを完全抹消")
        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)

    @with_command_limits
    async def context_flip(self, interaction: discord.Interaction, message: discord.Message):
        if not message.attachments:
            await interaction.response.send_message("このメッセージに画像が添付されていません。", ephemeral=True)
            return

        target = next((att for att in message.attachments if is_image_ext(get_extension(att.filename))), None)
        if not target:
            await interaction.response.send_message("画像ファイルが見つかりませんでした。", ephemeral=True)
            return

        ephemeral = get_ephemeral()
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        ext = get_extension(target.filename)
        try:
            raw_data = await target.read()
            validate_data_size(raw_data)
            out_bytes, out_ext = await asyncio.to_thread(process_flip, raw_data, "horizontal", ext)
        except Exception as e:
            await interaction.followup.send(f"反転失敗: {e}", ephemeral=True)
            return
        finally:
            gc.collect()

        out_name = f"flipped_{target.filename.rsplit('.', 1)[0]}.{out_ext}"
        discord_file = discord.File(io.BytesIO(out_bytes), filename=out_name)
        embed = discord.Embed(
            title="くるっと反転した姿に化けたよ！",
            description=(
                "**反転方向**: `左右反転`\n\n"
                "**保存方法**:\n"
                "・**スマホ**: 画像をタップして長押し ➔画像を保存\n"
                "・**PC**: 画像を右クリック ➔画像を保存"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"{BOT_NAME_JA} • 画像をタップ長押し／右クリックで保存できます")
        await interaction.followup.send(embed=embed, file=discord_file, ephemeral=ephemeral)


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageToolsCog(bot))
