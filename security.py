"""
添付ファイルの安全性チェックロジック。
1. 危険な拡張子のブロックリスト照合（即座に判定可能、追加コスト無し）
2. （任意）VirusTotal API によるハッシュベースの既知マルウェア照合
   - ファイル自体をアップロードするのではなく、SHA256ハッシュのみを照会するため
     ユーザーのファイル内容を外部に送信せずに済む（未知のファイルは「不明」判定になる）
   - VIRUSTOTAL_API_KEY が未設定の場合はこのチェックはスキップされる
"""
import hashlib
from dataclasses import dataclass
from typing import Optional

import aiohttp

from utils.file_helpers import get_extension
from config import DANGEROUS_EXTENSIONS, VIRUSTOTAL_API_KEY

VT_API_URL = "https://www.virustotal.com/api/v3/files/{sha256}"


@dataclass
class ScanResult:
    is_dangerous_extension: bool
    extension: str
    sha256: str
    vt_checked: bool = False
    vt_malicious_count: Optional[int] = None
    vt_suspicious_count: Optional[int] = None
    vt_error: Optional[str] = None

    @property
    def is_flagged(self) -> bool:
        if self.is_dangerous_extension:
            return True
        if self.vt_malicious_count and self.vt_malicious_count > 0:
            return True
        return False

    @property
    def is_suspicious(self) -> bool:
        return bool(self.vt_suspicious_count and self.vt_suspicious_count > 0)

    @property
    def reason(self) -> str:
        parts = []
        if self.is_dangerous_extension:
            parts.append(f"危険な拡張子 (.{self.extension})")
        if self.vt_malicious_count:
            parts.append(f"VirusTotalで{self.vt_malicious_count}件のエンジンがマルウェアと検出")
        if self.vt_suspicious_count:
            parts.append(f"VirusTotalで{self.vt_suspicious_count}件のエンジンが不審と判定")
        return " / ".join(parts) if parts else "問題なし"


def is_dangerous_extension(filename: str) -> bool:
    ext = get_extension(filename)
    return ext in DANGEROUS_EXTENSIONS


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def check_virustotal(session: aiohttp.ClientSession, sha256_hash: str) -> dict:
    """
    VirusTotal にSHA256ハッシュで既知のスキャン結果を照会する。
    戻り値: {"found": bool, "malicious": int, "suspicious": int} または {"error": str}
    ハッシュが未登録（=未知のファイル）の場合は found=False を返す（危険とは断定しない）。
    """
    if not VIRUSTOTAL_API_KEY:
        return {"error": "VIRUSTOTAL_API_KEY未設定"}

    headers = {"x-apikey": VIRUSTOTAL_API_KEY}
    url = VT_API_URL.format(sha256=sha256_hash)
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 404:
                return {"found": False, "malicious": 0, "suspicious": 0}
            if resp.status == 200:
                payload = await resp.json()
                stats = (
                    payload.get("data", {})
                    .get("attributes", {})
                    .get("last_analysis_stats", {})
                )
                return {
                    "found": True,
                    "malicious": stats.get("malicious", 0),
                    "suspicious": stats.get("suspicious", 0),
                }
            return {"error": f"VirusTotal APIへの照会に失敗しました (HTTP {resp.status})"}
    except aiohttp.ClientError as e:
        return {"error": "VirusTotalへの接続に失敗しました。"}
    except Exception as e:
        return {"error": "VirusTotal照会中に予期しないエラーが発生しました。"}


async def scan_attachment_bytes(
    filename: str, data: bytes, session: Optional[aiohttp.ClientSession] = None
) -> ScanResult:
    """添付ファイルのバイナリデータを受け取り、拡張子チェック+(任意)VirusTotalチェックを行う"""
    ext = get_extension(filename)
    sha256_hash = compute_sha256(data)
    result = ScanResult(
        is_dangerous_extension=ext in DANGEROUS_EXTENSIONS,
        extension=ext,
        sha256=sha256_hash,
    )

    if VIRUSTOTAL_API_KEY and session is not None:
        vt = await check_virustotal(session, sha256_hash)
        if "error" in vt:
            result.vt_error = vt["error"]
        else:
            result.vt_checked = True
            result.vt_malicious_count = vt.get("malicious", 0)
            result.vt_suspicious_count = vt.get("suspicious", 0)

    return result
