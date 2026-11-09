"""
「今回のコマンド実行の応答をephemeral(自分にだけ見える)にするか」を保持する。

with_command_limits デコレータが、ユーザーのプレミアム設定(utils.premium_store)を見て
このcontextvarをセットし、各コマンドの本体はget_ephemeral()で読んで
defer()/followup.send()のephemeral引数に渡す。

contextvarsを使っているのは、同時に複数人が別々のコマンドを実行していても、
asyncioのタスクごとに独立した値になり、互いに混ざらないようにするため
（単純なグローバル変数だと、同時実行時に別人の設定が混ざってしまう）。
"""
import contextvars

_ephemeral_var: contextvars.ContextVar[bool] = contextvars.ContextVar("command_ephemeral", default=False)


def get_ephemeral() -> bool:
    return _ephemeral_var.get()


def set_ephemeral(value: bool) -> None:
    _ephemeral_var.set(value)
