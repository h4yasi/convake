"""
管理者向けコマンドの権限チェック。

このBotは個人運用のため、「サーバーの管理権限を持つ人なら誰でも」ではなく、
config.OWNER_ID（あなたのDiscordユーザーID）のみに管理系コマンドの実行を限定する。
"""
import discord
from discord.ext import commands

from config import OWNER_ID


def is_owner_id(user_id: int) -> bool:
    return user_id == OWNER_ID


def is_owner(interaction: discord.Interaction) -> bool:
    return is_owner_id(interaction.user.id)


def owner_only():
    """
    プレフィックスコマンド(!コマンド)用のオーナー限定チェック。
    権限が無い場合はCheckFailureを送出するだけで、こちらからは何も応答しない
    （管理コマンドの存在を一般ユーザーに気付かせないため。main.pyのon_command_errorで
    CheckFailureは黙って無視している）。
    """
    async def predicate(ctx: commands.Context) -> bool:
        return is_owner_id(ctx.author.id)

    return commands.check(predicate)
