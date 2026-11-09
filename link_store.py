"""
/link 履歴の永続化（軽量JSONファイル）。

履歴にはCDN URLそのものではなく「保管用チャンネル内のメッセージID」を記録する。
DiscordのCDN URLは署名付きで一定時間後に期限切れになるが、メッセージIDさえあれば
APIでメッセージを取得し直すことで、いつでも新しい有効なURLを再発行できるため。

各レコードには作成時点で決めた「保存期限(expires_at)」を持たせる。
期限が来たレコードは purge_expired() で履歴から取り除く（cogs/link.py の定期掃除）。
このとき保管用チャンネルのメッセージ自体は削除せず残す（管理者が内容を確認できるように）。
履歴から外れたものは、Botからは再発行・削除ができなくなる。

記録は「本人のuser_id配下」にだけ保存し、検索・削除は必ず本人のuser_idで行う。
これにより、他人の履歴・他人が投稿したメッセージを操作できないようにしている。
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config import LINK_STORE_PATH, LINK_RETENTION_DAYS_PREMIUM

_lock = asyncio.Lock()


def _read() -> Dict[str, List[dict]]:
    if not os.path.exists(LINK_STORE_PATH):
        return {}
    try:
        with open(LINK_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write(data: Dict[str, List[dict]]) -> None:
    dir_name = os.path.dirname(LINK_STORE_PATH) or "."
    os.makedirs(dir_name, exist_ok=True)
    tmp_path = LINK_STORE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, LINK_STORE_PATH)


def parse_utc(raw: str) -> datetime:
    """ISO文字列をタイムゾーン付きUTC datetimeとして解釈する（naiveな値もUTC扱い）。"""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def expiry_of(record: dict) -> Optional[datetime]:
    """レコードの保存期限を返す。expires_atが無い古い形式のレコードは、作成日時から
    プレミアム用の保存期間で算出する（読めないレコードはNone）。"""
    try:
        if "expires_at" in record:
            return parse_utc(record["expires_at"])
        created = parse_utc(record["created_at"])
        return created + timedelta(days=LINK_RETENTION_DAYS_PREMIUM)
    except (KeyError, ValueError, TypeError):
        return None


async def count(user_id: int) -> int:
    async with _lock:
        data = await asyncio.to_thread(_read)
        return len(data.get(str(user_id), []))


async def is_full(user_id: int, max_records: int) -> bool:
    return await count(user_id) >= max_records


async def add(user_id: int, message_id: int, filename: str, kind: str, retention_days: int, max_records: int) -> bool:
    """履歴に追加する。上限に達している場合はFalse（追加しない）。"""
    async with _lock:
        data = await asyncio.to_thread(_read)
        records = data.setdefault(str(user_id), [])
        if len(records) >= max_records:
            return False
        now = datetime.now(timezone.utc)
        records.append(
            {
                "message_id": message_id,
                "filename": filename,
                "kind": kind,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=retention_days)).isoformat(),
            }
        )
        await asyncio.to_thread(_write, data)
        return True


async def list_records(user_id: int) -> List[dict]:
    """新しい順で返す。"""
    async with _lock:
        data = await asyncio.to_thread(_read)
        return list(reversed(data.get(str(user_id), [])))


async def get(user_id: int, message_id: int) -> Optional[dict]:
    target = str(message_id)
    for rec in await list_records(user_id):
        rec_id = rec.get("message_id")
        if rec_id is not None and str(rec_id) == target:
            return rec
    return None


async def remove(user_id: int, message_id: int) -> bool:
    target = str(message_id)
    async with _lock:
        data = await asyncio.to_thread(_read)
        key = str(user_id)
        records = data.get(key, [])
        remaining = [
            r for r in records
            if r.get("message_id") is None or str(r.get("message_id")) != target
        ]
        if len(remaining) == len(records):
            return False
        if remaining:
            data[key] = remaining
        else:
            data.pop(key, None)
        await asyncio.to_thread(_write, data)
        return True


async def purge_expired(now: datetime) -> int:
    """保存期限が過ぎた履歴を、まとめて1回の書き込みで取り除く。取り除いた件数を返す。

    取り除くのは「履歴の記録」だけで、保管用チャンネルのメッセージ自体は触らない
    （管理者が後から内容を確認できるよう、あえて残している）。
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    async with _lock:
        data = await asyncio.to_thread(_read)
        removed = 0
        for uid in list(data.keys()):
            kept = []
            for rec in data[uid]:
                exp = expiry_of(rec)
                if exp is not None and exp <= now:
                    removed += 1
                else:
                    kept.append(rec)
            if kept:
                data[uid] = kept
            else:
                del data[uid]
        if removed:
            await asyncio.to_thread(_write, data)
        return removed
