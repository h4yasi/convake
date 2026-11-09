"""
共通のファイル操作ユーティリティ。
- 添付ファイルの拡張子・種類判定
- 一時ディレクトリ管理
"""
import os

from config import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp", "tiff", "tif", "ico"}
PDF_EXTENSIONS = {"pdf"}
TEXT_EXTENSIONS = {"txt"}
CSV_EXTENSIONS = {"csv"}


def get_extension(filename: str) -> str:
    """ファイル名から拡張子（小文字・ドットなし）を取得する"""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def is_image_ext(ext: str) -> bool:
    return ext in IMAGE_EXTENSIONS


def ensure_temp_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


class FileLimitError(Exception):
    """入力または出力がBotの制限を超えた場合のエラー。"""


def validate_attachment_size(attachment) -> None:
    """Discordのサイズ情報を読み込み前に確認する。"""
    if attachment.size > MAX_INPUT_BYTES:
        raise FileLimitError(
            f"入力ファイルは{MAX_INPUT_BYTES / 1024 / 1024:.0f}MB以下にしてください。"
        )


def validate_data_size(data: bytes) -> None:
    """実際に取得したバイト列にも上限を適用する。"""
    if len(data) > MAX_INPUT_BYTES:
        raise FileLimitError(
            f"入力ファイルは{MAX_INPUT_BYTES / 1024 / 1024:.0f}MB以下にしてください。"
        )


def validate_output_size(data: bytes) -> None:
    if len(data) > MAX_OUTPUT_BYTES:
        raise FileLimitError(
            f"変換後のファイルが大きすぎます（上限 {MAX_OUTPUT_BYTES / 1024 / 1024:.0f}MB）。"
        )
