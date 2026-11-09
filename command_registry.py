"""
同期済みスラッシュコマンドの名前 -> IDのマッピング。

Discordのスラッシュコマンドをタップ1つで実行できるメンション形式
（</コマンド名:ID>）を組み立てるには、Discordに登録された後に割り振られる
コマンドIDが必要。IDはtree.sync()の戻り値でしか取得できないため、
main.pyのsetup_hookで同期した直後にここへ格納し、help.py等から参照する。
"""
from typing import Dict, Optional

_command_ids: Dict[str, int] = {}


def set_command_ids(mapping: Dict[str, int]) -> None:
    _command_ids.clear()
    _command_ids.update(mapping)


def get_command_id(name: str) -> Optional[int]:
    return _command_ids.get(name)


def mention(name: str, sub: str = "") -> str:
    """
    コマンドのタップ可能なメンション文字列を返す。
    まだ同期前でIDが無い場合は `/コマンド名` のプレーンテキストにフォールバックする
    （見た目は劣るが、少なくとも壊れた表示にはならない）。
    sub にはサブコマンド名（例: "settings"）を "コマンド サブコマンド" の形で渡す。
    """
    command_id = _command_ids.get(name)
    display = f"{name} {sub}".strip()
    if command_id is None:
        return f"`/{display}`"
    return f"</{display}:{command_id}>"
