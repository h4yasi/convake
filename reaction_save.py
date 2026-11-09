"""
指定したリアクション（デフォルト: 📌）がメッセージに付けられたら、
リアクションしたユーザーのDMにそのメッセージへのリンク（擬似的な保存機能）を送信する。

raw版のイベントを使うことで、Botが起動する前に投稿されたメッセージや
キャッシュされていないメッセージに対するリアクションにも対応できる。
"""
import discord
import logging
import time
from discord.ext import commands

from config import SAVE_REACTION_EMOJI

logger = logging.getLogger("discord_bot")


class ReactionSaveCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._recent_notifications: dict[tuple[int, int], float] = {}

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Bot自身のリアクションは無視
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        if str(payload.emoji) != SAVE_REACTION_EMOJI:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(
                payload.channel_id
            )
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        # リアクションの付け外しを繰り返した際のDMスパムを防止する。
        key = (payload.user_id, payload.message_id)
        now = time.monotonic()
        previous = self._recent_notifications.get(key)
        if previous is not None and now - previous < 300:
            return
        self._recent_notifications[key] = now
        if len(self._recent_notifications) > 1_000:
            cutoff = now - 300
            self._recent_notifications = {
                k: timestamp
                for k, timestamp in self._recent_notifications.items()
                if timestamp >= cutoff
            }

        # リアクションしたユーザーを取得
        user = payload.member
        if user is None:
            try:
                user = await self.bot.fetch_user(payload.user_id)
            except discord.HTTPException:
                return

        if user.bot:
            return

        jump_url = message.jump_url
        preview = message.content[:200] if message.content else "(テキストなし / 添付ファイルのみ)"

        embed = discord.Embed(
            title="メッセージを保存しました",
            description=preview,
            url=jump_url,
        )
        embed.add_field(name="リンク", value=jump_url, inline=False)
        embed.set_footer(text=f"#{getattr(channel, 'name', '不明なチャンネル')}")
        if message.author:
            embed.set_author(
                name=str(message.author),
                icon_url=message.author.display_avatar.url if hasattr(message.author, "display_avatar") else None,
            )

        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            logger.info("DM送信に失敗しました（DM拒否設定の可能性）: user_id=%s", payload.user_id)
        except discord.HTTPException as e:
            logger.warning("DM送信中にエラーが発生しました: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionSaveCog(bot))
