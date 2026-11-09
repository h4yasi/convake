"""
ffmpeg (システムコマンド) を利用した動画変換ロジック。
- 動画 -> GIF （パレット最適化による高画質2パス変換）
- GIF -> MP4

注意: このモジュールはffmpeg/ffprobeがシステムにインストールされていることが前提。
(Ubuntu/Debian: `sudo apt install ffmpeg`)
処理はブロッキングのsubprocess呼び出しのため、呼び出し側(cog)で
`asyncio.to_thread` 等を使いイベントループをブロックしないようにすること。
"""
import os
import logging
import subprocess
import tempfile
from typing import Optional

from config import (
    GIF_FPS,
    GIF_WIDTH,
    MAX_OUTPUT_BYTES,
    MAX_VIDEO_DURATION_SECONDS,
    MAX_VIDEO_HEIGHT,
    MAX_VIDEO_WIDTH,
)
from utils.file_helpers import validate_data_size, validate_output_size

logger = logging.getLogger("discord_bot")


class VideoConversionError(Exception):
    pass


def _run_ffmpeg(args: list, timeout: int = 120):
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise VideoConversionError(
            "ffmpegが見つかりません。サーバーにffmpegをインストールしてください。"
        )
    except subprocess.TimeoutExpired:
        raise VideoConversionError("変換処理がタイムアウトしました。")

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[-1500:]
        logger.warning("ffmpeg failed: %s", stderr)
        raise VideoConversionError("動画の変換に失敗しました。入力形式や動画コーデックを確認してください。")
    return result


def get_media_duration_seconds(path: str) -> Optional[float]:
    """ffprobeでメディアファイルの長さ(秒)を取得する。取得できない場合はNone"""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if result.returncode == 0:
            value = result.stdout.decode().strip()
            return float(value) if value else None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def get_media_dimensions(path: str) -> Optional[tuple[int, int]]:
    """最初の映像ストリームの幅・高さを取得する。取得できなければNone。"""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        if result.returncode == 0:
            lines = result.stdout.decode("utf-8", errors="replace").strip().splitlines()
            for line in lines:
                parts = line.strip().split("x", 1)
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    return int(parts[0]), int(parts[1])
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _validate_media(data: bytes, path: str) -> float:
    validate_data_size(data)
    duration = get_media_duration_seconds(path)
    dimensions = get_media_dimensions(path)
    if duration is None or dimensions is None:
        raise VideoConversionError("動画の長さまたは解像度を確認できないファイルは処理できません。")
    if duration <= 0 or duration > MAX_VIDEO_DURATION_SECONDS:
        raise VideoConversionError(
            f"動画の長さは{MAX_VIDEO_DURATION_SECONDS}秒以下にしてください。"
        )
    width, height = dimensions
    if width < 1 or height < 1 or width > MAX_VIDEO_WIDTH or height > MAX_VIDEO_HEIGHT:
        raise VideoConversionError(
            f"動画の解像度は{MAX_VIDEO_WIDTH}x{MAX_VIDEO_HEIGHT}以下にしてください。"
        )
    return duration


def convert_video_to_gif(
    data: bytes,
    fps: int = GIF_FPS,
    width: int = GIF_WIDTH,
    max_duration: int = MAX_VIDEO_DURATION_SECONDS,
) -> bytes:
    """
    動画をGIFに変換する。ffmpegのパレット生成(2パス)により高画質なGIFを作る。
    max_durationを超える動画は処理負荷対策のため拒否する。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = os.path.join(tmpdir, "input")
        out_path = os.path.join(tmpdir, "output.gif")
        palette_path = os.path.join(tmpdir, "palette.png")

        with open(in_path, "wb") as f:
            f.write(data)

        duration = _validate_media(data, in_path)
        if max_duration <= 0 or duration > max_duration:
            raise VideoConversionError(f"動画の長さは{max_duration}秒以下にしてください。")

        vf_scale = f"fps={fps},scale={width}:-1:flags=lanczos"

        # 1. パレット生成
        _run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", in_path,
                "-t", str(max_duration), "-fs", str(MAX_OUTPUT_BYTES),
                "-threads", "1", "-vf", f"{vf_scale},palettegen",
                palette_path,
            ]
        )
        # 2. パレットを使ってGIF生成
        _run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", in_path, "-i", palette_path,
                "-t", str(max_duration), "-fs", str(MAX_OUTPUT_BYTES),
                "-threads", "1",
                "-lavfi", f"{vf_scale}[x];[x][1:v]paletteuse",
                out_path,
            ]
        )

        if not os.path.exists(out_path):
            raise VideoConversionError("変換結果ファイルが生成されませんでした。")
        with open(out_path, "rb") as f:
            result = f.read()
        validate_output_size(result)
        return result


def convert_gif_to_mp4(data: bytes) -> bytes:
    """GIFをMP4(H.264)に変換する"""
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = os.path.join(tmpdir, "input.gif")
        out_path = os.path.join(tmpdir, "output.mp4")

        with open(in_path, "wb") as f:
            f.write(data)

        _validate_media(data, in_path)
        _run_ffmpeg(
            [
                "ffmpeg", "-y", "-i", in_path,
                "-t", str(MAX_VIDEO_DURATION_SECONDS),
                "-fs", str(MAX_OUTPUT_BYTES),
                "-threads", "1",
                "-movflags", "faststart",
                "-pix_fmt", "yuv420p",
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                out_path,
            ]
        )

        if not os.path.exists(out_path):
            raise VideoConversionError("変換結果ファイルが生成されませんでした。")
        with open(out_path, "rb") as f:
            result = f.read()
        validate_output_size(result)
        return result
