"""コマンド単位の利用制限。

ユーザー単位: 前回の対象コマンドが完了するまで次を受け付けない。
サーバー単位: コマンド受付時刻の間隔を一定秒数空ける（プレミアム会員は短縮版を適用）。
DM制限: プレミアム会員以外はDMでコマンドを使用できない。
ephemeral設定: プレミアム会員が事前に設定していれば、応答を自分にだけ見える形にする。
"""
import asyncio
import time
from contextlib import asynccontextmanager
from functools import wraps
from typing import Optional

import discord

from config import SERVER_COOLDOWN_SECONDS, PREMIUM_SERVER_COOLDOWN_SECONDS, PREMIUM_PROMOTION_MESSAGE
from utils import premium_store
from utils.ephemeral import set_ephemeral


class CommandCooldown(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


_state_lock = asyncio.Lock()
_active_users: set[int] = set()
_last_started_by_guild: dict[int, float] = {}
# _last_started_by_guildの掃除しきい値。Botが長期間多数のサーバーに出入りすると
# 抜けたサーバーのエントリが残り続けメモリを圧迫するため、一定件数を超えたら
# 直近のクールダウン期間内のものだけ残して間引く。
_GUILD_MAP_CLEANUP_THRESHOLD = 2_000


@asynccontextmanager
async def command_guard(user_id: int, guild_id: Optional[int], is_premium: bool = False):
    now = time.monotonic()
    cooldown = PREMIUM_SERVER_COOLDOWN_SECONDS if is_premium else SERVER_COOLDOWN_SECONDS
    async with _state_lock:
        if user_id in _active_users:
            raise CommandCooldown(
                "前回のコマンド処理が完了するまで、次のコマンドは実行できません。"
            )

        if guild_id is not None and cooldown:
            last_started = _last_started_by_guild.get(guild_id)
            if last_started is not None:
                retry_after = cooldown - (now - last_started)
                if retry_after > 0:
                    raise CommandCooldown(
                        f"サーバーのクールダウン中です。{retry_after:.1f}秒後に再試行してください。"
                    )

        _active_users.add(user_id)
        if guild_id is not None:
            _last_started_by_guild[guild_id] = now
            # クールダウン判定に使うのは直近SERVER_COOLDOWN_SECONDS秒以内のエントリだけなので、
            # それより古いものは間引いてよい(Botが抜けたサーバー等のエントリが残り続けるのを防ぐ)。
            if len(_last_started_by_guild) > _GUILD_MAP_CLEANUP_THRESHOLD:
                cutoff = now - SERVER_COOLDOWN_SECONDS
                stale_keys = [
                    gid for gid, ts in _last_started_by_guild.items() if ts < cutoff
                ]
                for gid in stale_keys:
                    del _last_started_by_guild[gid]

    try:
        yield
    finally:
        async with _state_lock:
            _active_users.discard(user_id)


def with_command_limits(func):
    """app_commandsの関数を、DM制限・ephemeral設定・ユーザー/サーバー制限付きで実行するデコレータ。"""
    @wraps(func)
    async def wrapped(self, interaction, *args, **kwargs):
        is_premium = await premium_store.is_active(interaction.user.id)

        # DM制限: プレミアム会員以外はDMでコマンドを使用できない
        if interaction.guild is None and not is_premium:
            embed = discord.Embed(
                description=(
                    f"DMではコマンドを使用できません。プレミアム会員はDMでもご利用いただけます。\n"
                    f"{PREMIUM_PROMOTION_MESSAGE}"
                ),
                color=discord.Color.gold(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # プレミアム会員が事前に設定していれば、以降このコマンド内の応答は自分にだけ見える形になる
        # （非会員は常にFalse＝公開のまま）
        ephemeral_pref = await premium_store.get_ephemeral_preference(interaction.user.id) if is_premium else False
        set_ephemeral(ephemeral_pref)

        try:
            async with command_guard(interaction.user.id, interaction.guild_id, is_premium):
                return await func(self, interaction, *args, **kwargs)
        except CommandCooldown as exc:
            if interaction.response.is_done():
                await interaction.followup.send(exc.message, ephemeral=True)
            else:
                await interaction.response.send_message(exc.message, ephemeral=True)

    return wrapped