"""
複数の画像を1枚の画像に結合するロジック（縦並び / 横並び / グリッド）
"""
import io
import math
from typing import List

from PIL import Image

from config import MAX_IMAGE_DIMENSION, MAX_IMAGE_FRAMES, MAX_IMAGE_PIXELS
from utils.file_helpers import validate_output_size


class MergeError(Exception):
    pass


def _load_images(images_data: List[bytes]) -> List[Image.Image]:
    images = []
    total_pixels = 0
    for data in images_data:
        try:
            img = Image.open(io.BytesIO(data))
            width, height = img.size
            if (
                width < 1
                or height < 1
                or width > MAX_IMAGE_DIMENSION
                or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise MergeError("画像サイズが上限を超えています。")
            if getattr(img, "n_frames", 1) > MAX_IMAGE_FRAMES:
                raise MergeError("画像のフレーム数が上限を超えています。")
            total_pixels += width * height
            if total_pixels > MAX_IMAGE_PIXELS:
                raise MergeError("結合する画像の総ピクセル数が上限を超えています。")
            img.load()
            images.append(img.convert("RGBA"))
        except MergeError:
            for opened in images:
                opened.close()
            raise
        except Exception as e:
            for opened in images:
                opened.close()
            raise MergeError("画像の読み込みに失敗しました。") from e
    return images


def _validate_canvas_size(width: int, height: int) -> None:
    if (
        width < 1
        or height < 1
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise MergeError("結合後の画像サイズが上限を超えています。")


def merge_vertical(images_data: List[bytes], gap: int = 4, bg=(255, 255, 255, 255)) -> bytes:
    """画像を縦に並べる。幅は最大幅に揃え、中央寄せする"""
    images = _load_images(images_data)
    max_width = max(img.width for img in images)
    total_height = sum(img.height for img in images) + gap * (len(images) - 1)
    _validate_canvas_size(max_width, total_height)

    canvas = Image.new("RGBA", (max_width, total_height), bg)
    try:
        y = 0
        for img in images:
            x = (max_width - img.width) // 2
            canvas.paste(img, (x, y), img)
            y += img.height + gap

        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=True)
        result = out.getvalue()
        validate_output_size(result)
        return result
    finally:
        for img in images:
            img.close()
        canvas.close()


def merge_horizontal(images_data: List[bytes], gap: int = 4, bg=(255, 255, 255, 255)) -> bytes:
    """画像を横に並べる。高さは最大高に揃え、中央寄せする"""
    images = _load_images(images_data)
    max_height = max(img.height for img in images)
    total_width = sum(img.width for img in images) + gap * (len(images) - 1)
    _validate_canvas_size(total_width, max_height)

    canvas = Image.new("RGBA", (total_width, max_height), bg)
    try:
        x = 0
        for img in images:
            y = (max_height - img.height) // 2
            canvas.paste(img, (x, y), img)
            x += img.width + gap

        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=True)
        result = out.getvalue()
        validate_output_size(result)
        return result
    finally:
        for img in images:
            img.close()
        canvas.close()


def merge_grid(
    images_data: List[bytes], columns: int = None, gap: int = 4, bg=(255, 255, 255, 255)
) -> bytes:
    """
    画像をグリッド状に並べる。
    各セルは画像群の中で最大の幅・高さに統一し（アスペクト比を保ったまま縮小し中央配置）、
    columns未指定の場合は枚数に応じて自動でほぼ正方形に近い配置にする。
    """
    images = _load_images(images_data)
    n = len(images)
    if columns is None or columns < 1:
        columns = math.ceil(math.sqrt(n))
    rows = math.ceil(n / columns)

    cell_w = max(img.width for img in images)
    cell_h = max(img.height for img in images)

    canvas_w = cell_w * columns + gap * (columns + 1)
    canvas_h = cell_h * rows + gap * (rows + 1)
    _validate_canvas_size(canvas_w, canvas_h)
    canvas = Image.new("RGBA", (canvas_w, canvas_h), bg)

    try:
        for idx, img in enumerate(images):
            row = idx // columns
            col = idx % columns
            # セル内に収まるようアスペクト比を保って縮小
            scale = min(cell_w / img.width, cell_h / img.height, 1.0)
            new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
            resized = img.resize(new_size, Image.LANCZOS)

            cell_x = gap + col * (cell_w + gap)
            cell_y = gap + row * (cell_h + gap)
            offset_x = cell_x + (cell_w - resized.width) // 2
            offset_y = cell_y + (cell_h - resized.height) // 2
            canvas.paste(resized, (offset_x, offset_y), resized)
            resized.close()

        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=True)
        result = out.getvalue()
        validate_output_size(result)
        return result
    finally:
        for img in images:
            img.close()
        canvas.close()


def merge_images(images_data: List[bytes], layout: str, columns: int = None) -> bytes:
    layout = layout.lower()
    if len(images_data) < 2:
        raise MergeError("結合には2枚以上の画像が必要です。")
    if layout in ("vertical", "縦", "v"):
        return merge_vertical(images_data)
    elif layout in ("horizontal", "横", "h"):
        return merge_horizontal(images_data)
    elif layout in ("grid", "グリッド", "g"):
        return merge_grid(images_data, columns=columns)
    else:
        raise MergeError(f"未対応のレイアウトです: {layout} (vertical / horizontal / grid から選択してください)")
