"""
Bot全体の起動時刻・直近のエラーを記録する軽量なインメモリモジュール。
ステータス埋め込み(cogs/status.py)から参照される。

永続化はしない（Botが再起動したら「直近のエラー」もリセットされる想定でよい）。
"""
from collections import deque
from datetime import datetime, timezone
from typing import Deque, List, Tuple

_START_TIME = datetime.now(timezone.utc)
_MAX_RECENT_ERRORS = 5
_recent_errors: Deque[Tuple[datetime, str]] = deque(maxlen=_MAX_RECENT_ERRORS)


def record_error(summary: str) -> None:
    """直近のエラーとして記録する（最大5件、古いものから捨てられる）。"""
    # メッセージが長すぎるとEmbedを圧迫するので切り詰める
    _recent_errors.append((datetime.now(timezone.utc), summary[:200]))


def get_recent_errors() -> List[Tuple[datetime, str]]:
    return list(_recent_errors)


def get_start_time() -> datetime:
    return _START_TIME
