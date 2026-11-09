"""
複数画像を1枚に結合するスラッシュコマンド。
Discordのスラッシュコマンドは「以前のメッセージへの返信」で添付を参照できないため、
結合したい画像は image1〜image5 のパラメータに直接添付してもらう方式にしている。

使い方の例:
  /merge layout:grid columns:2 image1:<添付> image2:<添付> image3:<添付> image4:<添付>
"""
import gc
import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import MAX_MERGE_INPUT_BYTES, MAX_MERGE_IMAGES_FREE, MAX_MERGE_IMAGES_PREMIUM, PREMIUM_PROMOTION_MESSAGE
from utils.file_helpers import FileLimitError, validate_attachment_size, validate_data_size
from utils.merger import merge_images, MergeError
from utils.concurrency import run_cpu_bound, heavy_task_guard, PRIORITY_PREMIUM, PRIORITY_FREE
from utils.rate_limit import with_command_limits
from utils import premium_store
from utils.ephemeral import get_ephemeral

logger = logging.getLogger("discord_bot")


class MergeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="merge", description="複数の画像を1枚に結合します（縦/横/グリッド）")
    @with_command_limits
    @app_commands.describe(
        layout="結合レイアウトを選択してください",
        image1="1枚目の画像",
        image2="2枚目の画像",
        image3="3枚目の画像",
        image4="4枚目の画像（プレミアム会員限定）",
        image5="5枚目の画像（プレミアム会員限定）",
        columns="グリッドの列数（layoutがgridの場合のみ有効、未指定で自動）",
    )
    @app_commands.choices(
        layout=[
            # Discordの選択肢に表示される名前だけ日本語にし、内部で使う値(merge_images側が
            # 期待する文字列)はそのまま英語にしている（コード側のロジック変更が不要なため）。
            app_commands.Choice(name="縦に結合", value="vertical"),
            app_commands.Choice(name="横に結合", value="horizontal"),
            app_commands.Choice(name="グリッド", value="grid"),
        ]
    )
    async def merge(
        self,
        interaction: discord.Interaction,
        layout: app_commands.Choice[str],
        image1: discord.Attachment,
        image2: discord.Attachment,
        image3: Optional[discord.Attachment] = None,
        image4: Optional[discord.Attachment] = None,
        image5: Optional[discord.Attachment] = None,
        columns: Optional[app_commands.Range[int, 1, 5]] = None,
    ):
        attachments = [a for a in [image1, image2, image3, image4, image5] if a is not None]

        is_premium = await premium_store.is_active(interaction.user.id)
        limit = MAX_MERGE_IMAGES_PREMIUM if is_premium else MAX_MERGE_IMAGES_FREE
        if len(attachments) > limit:
            message = f"結合できる画像は最大{limit}枚までです。"
            if not is_premium and MAX_MERGE_IMAGES_PREMIUM > MAX_MERGE_IMAGES_FREE:
                message += (
                    f"\nプレミアム会員になると最大{MAX_MERGE_IMAGES_PREMIUM}枚まで結合できます。\n"
                    f"{PREMIUM_PROMOTION_MESSAGE}"
                )
                embed = discord.Embed(description=message, color=discord.Color.gold())
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return

        try:
            for attachment in attachments:
                validate_attachment_size(attachment)
        except FileLimitError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=get_ephemeral())
        try:
            async with heavy_task_guard(PRIORITY_PREMIUM if is_premium else PRIORITY_FREE):
                images_data = [await a.read() for a in attachments]
                for data in images_data:
                    validate_data_size(data)
                if sum(len(data) for data in images_data) > MAX_MERGE_INPUT_BYTES:
                    raise FileLimitError(
                        f"結合するファイルの合計は{MAX_MERGE_INPUT_BYTES / 1024 / 1024:.0f}MB以下にしてください。"
                    )
                result = await run_cpu_bound(merge_images, images_data, layout=layout.value, columns=columns)
                await interaction.followup.send(
                    content=f"**{len(attachments)}枚の画像が1枚に化けたよ！（{layout.name}）**",
                    file=discord.File(io.BytesIO(result), filename="merged.png"),
                    ephemeral=get_ephemeral(),
                )
        except MergeError as e:
            await interaction.followup.send(f"結合エラー: {e}", ephemeral=get_ephemeral())
        except FileLimitError as e:
            await interaction.followup.send(str(e), ephemeral=True)
        except Exception:
            logger.exception("merge command failed")
            await interaction.followup.send(
                "結合中に予期しないエラーが発生しました。入力ファイルを確認してください。",
                ephemeral=True,
            )
        finally:
            gc.collect()


async def setup(bot: commands.Bot):
    await bot.add_cog(MergeCog(bot))
