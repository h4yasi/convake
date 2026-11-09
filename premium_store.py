"""
プレミアム会員情報の永続化。

人数規模的にDBを導入するほどではないため、単純なJSONファイル + asyncio.Lockで実装する。
書き込みは一時ファイルに書いてから os.replace で置き換えることで、
書き込み中にBotが落ちてもファイルが壊れないようにしている。
"""
import asyncio
import calendar
import json
import os
from datetime import datetime, timezone
from typing import Dict, Optional

from config import PREMIUM_STORE_PATH

_lock = asyncio.Lock()


def parse_utc(raw: str) -> datetime:
    """ISO文字列をタイムゾーン付きUTC datetimeとして解釈する（naiveな値もUTC扱い）。"""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _add_months(dt: datetime, months: int) -> datetime:
    """dtにmonthsヶ月を加算する（月末日のずれを吸収する）。"""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _read() -> Dict[str, dict]:
    if not os.path.exists(PREMIUM_STORE_PATH):
        return {}
    try:
        with open(PREMIUM_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _write(data: Dict[str, dict]) -> None:
    dir_name = os.path.dirname(PREMIUM_STORE_PATH) or "."
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = PREMIUM_STORE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, PREMIUM_STORE_PATH)


async def grant(user_id: int, guild_id: int, months: int) -> str:
    """
    ユーザーをプレミアムとして登録する。
    既に有効期限内で登録されている場合は、現在の期限からさらに加算する（更新扱い）。
    登録後の有効期限（ISO文字列）を返す。
    """
    async with _lock:
        data = await asyncio.to_thread(_read)
        key = str(user_id)
        now = datetime.now(timezone.utc)
        existing = data.get(key)
        base = now
        if existing:
            try:
                current_expiry = parse_utc(existing["expires_at"])
                if current_expiry > now:
                    base = current_expiry
            except (KeyError, ValueError, TypeError):
                pass
        expires_at = _add_months(base, months)
        # 既存の ephemeral 設定は更新（月数延長）時も保持する。
        # ここで上書きしてしまうと、更新のたびに「応答を自分にだけ見える形にする」設定が
        # 黙って公開に戻ってしまい、本人が気付かないまま意図せず公開状態になる
        # （プライバシー上望ましくない）ため、必ず引き継ぐ。
        preserved_ephemeral = bool(existing.get("ephemeral", False)) if existing else False
        data[key] = {
            "guild_id": guild_id,
            "granted_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "months": months,
            "reminded": False,
            "ephemeral": preserved_ephemeral,
        }
        await asyncio.to_thread(_write, data)
        return data[key]["expires_at"]


async def revoke(user_id: int) -> bool:
    """登録を削除する。削除できた場合True。"""
    async with _lock:
        data = await asyncio.to_thread(_read)
        key = str(user_id)
        if key not in data:
            return False
        del data[key]
        await asyncio.to_thread(_write, data)
        return True


async def get(user_id: int) -> Optional[dict]:
    data = await get_all()
    return data.get(str(user_id))


async def is_active(user_id: int) -> bool:
    """
    現在有効なプレミアム会員かどうかを判定する。
    ロールに依存せず、このユーザーID自体をチェックするため、
    Botがどのサーバーで使われていても(DMでも)同じ結果になる。
    """
    info = await get(user_id)
    if not info:
        return False
    try:
        expires_at = parse_utc(info["expires_at"])
    except (KeyError, ValueError, TypeError):
        return False
    return expires_at > datetime.now(timezone.utc)


async def get_ephemeral_preference(user_id: int) -> bool:
    """
    プレミアム会員が設定した「応答をephemeralにするか」の設定を取得する。
    非会員・期限切れの場合は常にFalse（公開）を返す
    （＝ephemeralは有効なプレミアム会員だけが選べる設定）。
    """
    info = await get(user_id)
    if not info:
        return False
    try:
        expires_at = parse_utc(info["expires_at"])
    except (KeyError, ValueError, TypeError):
        return False
    if expires_at <= datetime.now(timezone.utc):
        return False
    return bool(info.get("ephemeral", False))


async def set_ephemeral_preference(user_id: int, ephemeral: bool) -> bool:
    """
    プレミアム会員のみ設定可能。登録データが無ければFalse（失敗）を返す。
    """
    async with _lock:
        data = await asyncio.to_thread(_read)
        key = str(user_id)
        if key not in data:
            return False
        data[key]["ephemeral"] = ephemeral
        await asyncio.to_thread(_write, data)
        return True


async def get_all() -> Dict[str, dict]:
    async with _lock:
        return await asyncio.to_thread(_read)


async def mark_reminded(user_id: int) -> None:
    async with _lock:
        data = await asyncio.to_thread(_read)
        key = str(user_id)
        if key in data:
            data[key]["reminded"] = True
            await asyncio.to_thread(_write, data)
