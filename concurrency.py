"""
メモリ・CPU負荷の高い処理が同時に大量実行されるとメモリ使用量が積み上がってしまうため、
優先度付きセマフォで同時実行数を制限する。

- heavy_task_semaphore: 動画変換(ffmpeg)や画像処理など、CPU/メモリを多く使う処理

セマフォの上限に達している間、後続のリクエストは自動的に順番待ちになる
（エラーにはならず、順番が来ると処理が開始される）。

通常のasyncio.Semaphoreは完全にFIFO（早い者勝ち）だが、プレミアム会員の体感速度を
上げるため、待ち行列に並んでいる間はプレミアム会員が優先的に順番を回されるように
独自実装している。重要なのは、同時実行数の上限（MAX_CONCURRENT_HEAVY_TASKS）自体は
一切変えていないこと。つまりBot全体の負荷は増えず、「複数人が同時に使っていて
順番待ちが発生している時だけ、プレミアム会員が先に処理される」という体感速度の違いだけを生む。
"""
import asyncio
import heapq
from contextlib import asynccontextmanager

from config import MAX_CONCURRENT_HEAVY_TASKS

# 優先度: 数値が小さいほど先に処理される
PRIORITY_PREMIUM = 0
PRIORITY_FREE = 1


class _PriorityHeavyTaskSemaphore:
    def __init__(self, value: int):
        self._value = value
        self._waiters: list[tuple[int, int, asyncio.Future]] = []  # (priority, 到着順, future)
        self._seq = 0

    async def acquire(self, priority: int = PRIORITY_FREE) -> None:
        if self._value > 0:
            self._value -= 1
            return
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._seq += 1
        # 同じpriorityの中ではFIFO（到着順）を保つため、seqを第2キーにしている
        heapq.heappush(self._waiters, (priority, self._seq, fut))
        try:
            await fut
        except asyncio.CancelledError:
            # 待機中にキャンセルされた場合、既にヒープに残っていれば取り除く
            try:
                self._waiters.remove((priority, self._seq, fut))
                heapq.heapify(self._waiters)
            except ValueError:
                # 既にreleaseされてポップ済みだった場合は、枠を1つ返しておく
                self._value += 1
            raise

    def release(self) -> None:
        while self._waiters:
            _, _, fut = heapq.heappop(self._waiters)
            if not fut.done():
                fut.set_result(None)
                return
        self._value += 1


heavy_task_semaphore = _PriorityHeavyTaskSemaphore(MAX_CONCURRENT_HEAVY_TASKS)


@asynccontextmanager
async def heavy_task_guard(priority: int = PRIORITY_FREE):
    """
    重い処理1件分の作業全体（添付ファイルの読み込み〜変換処理〜結果の破棄まで）を
    このガードで囲むためのコンテキストマネージャ。

    priority: PRIORITY_PREMIUM を渡すと、待ち行列に並んでいる間、他の(PRIORITY_FREEの)
    待機者より先に順番が回ってくる。同時実行数の上限自体は変わらないため、
    空いている時間帯は無料会員・プレミアム会員で速度差は出ない。

    重要: 添付ファイルの読み込み(file.read())は必ずこのガードの中で行うこと。
    もしガードの外で読み込んでからセマフォ待ちに入ると、同時実行数を制限していても
    「順番待ち中のリクエストが読み込み済みの入力データをメモリに保持し続ける」状態になり、
    悪意あるユーザーが多数のリクエストを同時に送ることで、実処理の同時実行数を絞っていても
    メモリを枯渇させられてしまう（同時実行数の制限が骨抜きになる）。
    """
    await heavy_task_semaphore.acquire(priority)
    try:
        yield
    finally:
        heavy_task_semaphore.release()


async def run_cpu_bound(function, *args, **kwargs):
    """
    CPU/メモリ負荷の高い同期処理を別スレッドで実行する。

    呼び出し側で既に `async with heavy_task_guard():` に入っている前提のため、
    ここではセマフォを取得しない（二重取得すると、同時実行数1の設定時に
    自分自身の順番待ちで永久に止まってしまう）。
    """
    return await asyncio.to_thread(function, *args, **kwargs)
