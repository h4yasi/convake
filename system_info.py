"""
プロセスのメモリ使用量など、OSレベルの簡易情報を取得するヘルパー。
/botstatus（cogs/diagnostics.py）と自動ステータス埋め込み（cogs/status.py）の
両方から共通で使われる。
"""
import platform
from typing import Optional

try:
    import resource
except ImportError:  # Windowsなど resource モジュールが無い環境向けフォールバック
    resource = None


def get_peak_memory_mb() -> Optional[float]:
    """
    プロセスの最大常駐メモリ量(概算)をMB単位で返す。
    重要: これは「起動してから今までの最大値」であり、絶対に減らない値。
    一時的に重い処理をした後は、実際の使用量が下がっていてもこの値は高いまま残る。
    「今、実際にどれだけ使っているか」を見たい場合は get_current_memory_mb() を使うこと。
    Linux/macOS(resourceモジュールが使える環境)でのみ取得可能。
    """
    if resource is None:
        return None
    usage_kb_or_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # LinuxはKB単位、macOS(Darwin)はバイト単位で返る仕様の違いを吸収
    if platform.system() == "Darwin":
        return usage_kb_or_bytes / (1024 * 1024)
    return usage_kb_or_bytes / 1024


def get_current_memory_mb() -> Optional[float]:
    """
    現在の常駐メモリ量(RSS)をMB単位で返す。get_peak_memory_mbと違い、
    メモリが解放されれば実際に減る「今の値」。
    /proc/self/status が存在する環境（Linux）でのみ取得可能。
    """
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None
