"""
画像加工・ユーティリティロジック（最適化・脆弱性耐性強化版）。
400MB〜500MB前後の低スペック環境でも安全に動作するよう、
サムネイル前処理、Pillowリソース制限、明示的リソース解放を実装。
"""
import io
import gc
from typing import Tuple, List
from PIL import Image, ImageOps, ImageDraw, ImageFont

from config import MAX_IMAGE_PIXELS, MAX_IMAGE_DIMENSION
from utils.file_helpers import validate_output_size

# Decompression Bomb（巨大ピクセル画像によるメモリ枯渇攻撃）防止
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _get_quantize_method():
    """Pillowバージョン間の互換性を担保した減色アルゴリズム（MEDIANCUT）の取得"""
    if hasattr(Image, "Quantize") and hasattr(Image.Quantize, "MEDIANCUT"):
        return Image.Quantize.MEDIANCUT
    return 0  # Pillow fallback (0 = MEDIANCUT)


def _get_rgba_quantize_method():
    """RGBA画像向け減色アルゴリズム（FASTOCTREE）の取得"""
    if hasattr(Image, "Quantize") and hasattr(Image.Quantize, "FASTOCTREE"):
        return Image.Quantize.FASTOCTREE
    return 2  # Pillow fallback (2 = FASTOCTREE)


def _quantize_rgba_under_limit(img: Image.Image, max_bytes: int, initial_colors: int) -> bytes:
    """RGBA画像を透過情報を保ったまま指定バイト数以下に減色する"""
    method = _get_rgba_quantize_method()
    out_bytes = b""
    for colors in (initial_colors, max(32, initial_colors // 2), 32):
        quantized = img.quantize(colors=colors, method=method)
        try:
            buf = io.BytesIO()
            quantized.save(buf, format="PNG", optimize=True)
            out_bytes = buf.getvalue()
            if len(out_bytes) <= max_bytes:
                return out_bytes
        finally:
            quantized.close()
    return out_bytes


def _safe_open_image(data: bytes) -> Image.Image:
    """画像を安全に開き、サイズ検証と向き補正を行う"""
    img = Image.open(io.BytesIO(data))
    w, h = img.size
    if w < 1 or h < 1 or w > MAX_IMAGE_DIMENSION or h > MAX_IMAGE_DIMENSION or w * h > MAX_IMAGE_PIXELS:
        img.close()
        raise ValueError(f"画像サイズが制限を超えています（最大{MAX_IMAGE_DIMENSION}px、{MAX_IMAGE_PIXELS:,}ピクセル）。")

    try:
        transposed = ImageOps.exif_transpose(img)
        if transposed is not None and transposed is not img:
            img.close()
            img = transposed
    except Exception:
        pass  # 破損したExifタグ等によるクラッシュを回避
    return img


def process_emoji(data: bytes, fit_mode: str = "crop") -> bytes:
    """Discordカスタム絵文字用画像（最大128KB, 128x128px, PNG）を生成する"""
    with _safe_open_image(data) as img:
        img = img.convert("RGBA")
        w, h = img.size

        if fit_mode == "pad":
            # 透明パディングで正方形化
            size = max(w, h)
            new_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            offset = ((size - w) // 2, (size - h) // 2)
            new_img.paste(img, offset)
            img = new_img
        else:
            # 中央クロップで正方形化
            min_edge = min(w, h)
            left = (w - min_edge) // 2
            top = (h - min_edge) // 2
            img = img.crop((left, top, left + min_edge, top + min_edge))

        img = img.resize((128, 128), Image.Resampling.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        out_bytes = out.getvalue()

        # Discord絵文字の上限128KB（131,072バイト）
        if len(out_bytes) > 128 * 1024:
            out_bytes = _quantize_rgba_under_limit(img, 128 * 1024, 128)

        validate_output_size(out_bytes)
        return out_bytes


def process_sticker(data: bytes, fit_mode: str = "crop") -> bytes:
    """Discordスタンプ用画像（最大512KB, 320x320px, PNG）を生成する"""
    with _safe_open_image(data) as img:
        img = img.convert("RGBA")
        w, h = img.size

        if fit_mode == "pad":
            size = max(w, h)
            new_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            offset = ((size - w) // 2, (size - h) // 2)
            new_img.paste(img, offset)
            img = new_img
        else:
            min_edge = min(w, h)
            left = (w - min_edge) // 2
            top = (h - min_edge) // 2
            img = img.crop((left, top, left + min_edge, top + min_edge))

        img = img.resize((320, 320), Image.Resampling.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        out_bytes = out.getvalue()

        # Discordスタンプの上限512KB
        if len(out_bytes) > 512 * 1024:
            out_bytes = _quantize_rgba_under_limit(img, 512 * 1024, 256)

        validate_output_size(out_bytes)
        return out_bytes


def process_avatar_crop(data: bytes) -> bytes:
    """Discordプロフィールアイコン用に、四角い画像を円形（透過PNG）に切り抜く"""
    with _safe_open_image(data) as img:
        img = img.convert("RGBA")
        w, h = img.size

        # 中央正方形切り抜き
        min_edge = min(w, h)
        left = (w - min_edge) // 2
        top = (h - min_edge) // 2
        img = img.crop((left, top, left + min_edge, top + min_edge))

        # アバター用として過大ならリサイズ（メモリ節約・上限1024px）
        if min_edge > 1024:
            img = img.resize((1024, 1024), Image.Resampling.LANCZOS)
            min_edge = 1024

        # 2倍サイズで滑らかな円形マスクを描画して縮小（アンチエイリアス処理）
        mask_scale = 2
        mask = Image.new("L", (min_edge * mask_scale, min_edge * mask_scale), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, min_edge * mask_scale - 1, min_edge * mask_scale - 1), fill=255)
        mask = mask.resize((min_edge, min_edge), Image.Resampling.LANCZOS)

        img.putalpha(mask)

        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        result = out.getvalue()
        validate_output_size(result)
        return result


def process_strip_exif(data: bytes, original_ext: str) -> Tuple[bytes, str]:
    """位置情報（GPS）や撮影機材情報などのExifメタデータを完全抹消して再保存する"""
    with _safe_open_image(data) as img:
        # 全メタデータ情報を初期化（Exif, GPS, ICCプロフィール, コメント等を除去）
        clean_img = img.copy()
        clean_img.info.clear()

        out = io.BytesIO()
        ext = original_ext.lower()
        if ext in ("jpg", "jpeg"):
            clean_img = clean_img.convert("RGB")
            clean_img.save(out, format="JPEG", quality=92, optimize=True)
            out_ext = "jpg"
        elif ext == "webp":
            clean_img.save(out, format="WEBP", quality=92)
            out_ext = "webp"
        else:
            # 透過情報があればRGBA、なければRGBで透過保持
            if clean_img.mode in ("RGBA", "LA") or (clean_img.mode == "P" and "transparency" in clean_img.info):
                clean_img = clean_img.convert("RGBA")
            else:
                clean_img = clean_img.convert("RGB")
            clean_img.save(out, format="PNG", optimize=True)
            out_ext = "png"

        result = out.getvalue()
        validate_output_size(result)
        return result, out_ext


def process_palette(data: bytes, num_colors: int = 6) -> Tuple[bytes, List[str]]:
    """画像から主要カラーを抽出し、カラーパレット画像とHEXコード一覧を生成する"""
    with _safe_open_image(data) as img:
        img = img.convert("RGB")

        # 低メモリ対策: 120x120pxにサムネイル化してから減色処理を行う（メモリ消費1MB未満）
        img.thumbnail((120, 120), Image.Resampling.BILINEAR)

        num_colors = max(3, min(num_colors, 8))
        quantized = img.quantize(colors=num_colors, method=_get_quantize_method())
        palette = quantized.getpalette() or []

        color_counts = quantized.getcolors() or []
        color_counts.sort(key=lambda x: x[0], reverse=True)

        hex_colors: List[str] = []
        rgb_colors = []
        for _, idx in color_counts[:num_colors]:
            if idx * 3 + 2 < len(palette):
                r = palette[idx * 3]
                g = palette[idx * 3 + 1]
                b = palette[idx * 3 + 2]
                hex_code = f"#{r:02X}{g:02X}{b:02X}"
                if hex_code not in hex_colors:
                    hex_colors.append(hex_code)
                    rgb_colors.append((r, g, b))

        # 万一色が抽出できなかった場合のゼロ除算・空リスト防止フォールバック
        if not rgb_colors:
            rgb_colors = [(43, 45, 49), (88, 101, 242), (87, 242, 135)]
            hex_colors = ["#2B2D31", "#5865F2", "#57F287"]

        # パレットカード画像を生成（幅600px x 高さ140px）
        width = 600
        height = 140
        card = Image.new("RGB", (width, height), (28, 30, 36))
        draw = ImageDraw.Draw(card)

        count = max(1, len(rgb_colors))
        swatch_margin = 16
        swatch_width = max(10, (width - (swatch_margin * (count + 1))) // count)
        swatch_height = 100
        top = 20

        for i, (r, g, b) in enumerate(rgb_colors):
            x0 = swatch_margin + i * (swatch_width + swatch_margin)
            y0 = top
            x1 = x0 + swatch_width
            y1 = y0 + swatch_height

            draw.rectangle([x0, y0, x1, y1], fill=(r, g, b))
            # 明るい色には境界線を引いて視認性を向上
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            if lum > 210:
                draw.rectangle([x0, y0, x1, y1], outline=(180, 180, 180), width=1)

        out = io.BytesIO()
        card.save(out, format="PNG", optimize=True)
        result = out.getvalue()
        validate_output_size(result)
        return result, hex_colors


def process_flip(data: bytes, direction: str = "horizontal", original_ext: str = "png") -> Tuple[bytes, str]:
    """画像を左右反転（水平ミラー）または上下反転（垂直フリップ）する"""
    with _safe_open_image(data) as img:
        if direction == "vertical":
            flipped = ImageOps.flip(img)
        else:
            flipped = ImageOps.mirror(img)

        out = io.BytesIO()
        ext = original_ext.lower()
        if ext in ("jpg", "jpeg"):
            flipped = flipped.convert("RGB")
            flipped.save(out, format="JPEG", quality=92, optimize=True)
            out_ext = "jpg"
        elif ext == "webp":
            flipped.save(out, format="WEBP", quality=92)
            out_ext = "webp"
        else:
            if flipped.mode in ("RGBA", "LA") or (flipped.mode == "P" and "transparency" in flipped.info):
                flipped = flipped.convert("RGBA")
            else:
                flipped = flipped.convert("RGB")
            flipped.save(out, format="PNG", optimize=True)
            out_ext = "png"

        result = out.getvalue()
        validate_output_size(result)
        return result, out_ext


def _get_watermark_font(size: int):
    """フォントファイルを安全に読み込む（見つからなければPillowデフォルトにフォールバック）"""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "DejaVuSans.ttf",
        "arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def process_watermark(
    data: bytes,
    text: str,
    position: str = "bottom_right",
    opacity_level: str = "medium",
    original_ext: str = "png",
) -> Tuple[bytes, str]:
    """画像に文字ウォーターマーク（透かし・サイン）を合成する"""
    with _safe_open_image(data) as img:
        base = img.convert("RGBA")
        w, h = base.size

        # 透明度の数値マッピング
        alpha_map = {
            "light": 70,     # 約27%
            "medium": 130,   # 約51%
            "strong": 200,   # 約78%
        }
        alpha = alpha_map.get(opacity_level, 130)

        clean_text = text.strip()[:80] or "WATERMARK"
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        if position == "center":
            # 中央に大きめの斜め透かし
            font_size = max(18, min(w, h) // 10)
            font = _get_watermark_font(font_size)

            temp_draw = ImageDraw.Draw(overlay)
            bbox = temp_draw.textbbox((0, 0), clean_text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            pad = 20
            stamp_w = text_w + pad * 2
            stamp_h = text_h + pad * 2
            stamp = Image.new("RGBA", (stamp_w, stamp_h), (0, 0, 0, 0))
            stamp_draw = ImageDraw.Draw(stamp)

            # 視認性向上のための影（黒）と本体（白）
            stamp_draw.text((pad + 1, pad + 1), clean_text, font=font, fill=(0, 0, 0, int(alpha * 0.7)))
            stamp_draw.text((pad, pad), clean_text, font=font, fill=(255, 255, 255, alpha))

            rotated_stamp = stamp.rotate(30, expand=True, resample=Image.Resampling.BICUBIC)
            rw, rh = rotated_stamp.size
            offset = ((w - rw) // 2, (h - rh) // 2)
            overlay.paste(rotated_stamp, offset, rotated_stamp)

        elif position == "repeat":
            # 全面に格子状に斜め透かし（無断転載防止パターン）
            font_size = max(14, min(w, h) // 22)
            font = _get_watermark_font(font_size)

            temp_draw = ImageDraw.Draw(overlay)
            bbox = temp_draw.textbbox((0, 0), clean_text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            step_x = max(120, text_w + 60)
            step_y = max(80, text_h + 60)

            stamp = Image.new("RGBA", (text_w + 10, text_h + 10), (0, 0, 0, 0))
            stamp_draw = ImageDraw.Draw(stamp)
            stamp_draw.text((3, 3), clean_text, font=font, fill=(0, 0, 0, int(alpha * 0.5)))
            stamp_draw.text((2, 2), clean_text, font=font, fill=(255, 255, 255, int(alpha * 0.7)))
            rotated_stamp = stamp.rotate(25, expand=True, resample=Image.Resampling.BICUBIC)
            rw, rh = rotated_stamp.size

            for y in range(-rh, h + rh, step_y):
                x_offset = (step_x // 2) if (y // step_y) % 2 == 1 else 0
                for x in range(-rw + x_offset, w + rw, step_x):
                    overlay.paste(rotated_stamp, (x, y), rotated_stamp)

        else:
            # bottom_right (右下サイン) - デフォルト
            font_size = max(14, min(w, h) // 18)
            font = _get_watermark_font(font_size)
            draw = ImageDraw.Draw(overlay)

            bbox = draw.textbbox((0, 0), clean_text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            margin = max(10, min(w, h) // 30)
            x = max(5, w - text_w - margin)
            y = max(5, h - text_h - margin)

            # 半透明背景座布団（角丸プレート）
            plate_pad_x = 8
            plate_pad_y = 5
            plate_box = [x - plate_pad_x, y - plate_pad_y, x + text_w + plate_pad_x, y + text_h + plate_pad_y]
            draw.rounded_rectangle(plate_box, radius=6, fill=(0, 0, 0, int(alpha * 0.45)))

            # 文字描画
            draw.text((x + 1, y + 1), clean_text, font=font, fill=(0, 0, 0, int(alpha * 0.8)))
            draw.text((x, y), clean_text, font=font, fill=(255, 255, 255, alpha))

        composed = Image.alpha_composite(base, overlay)

        out = io.BytesIO()
        ext = original_ext.lower()
        if ext in ("jpg", "jpeg"):
            composed = composed.convert("RGB")
            composed.save(out, format="JPEG", quality=92, optimize=True)
            out_ext = "jpg"
        elif ext == "webp":
            composed.save(out, format="WEBP", quality=92)
            out_ext = "webp"
        else:
            composed.save(out, format="PNG", optimize=True)
            out_ext = "png"

        result = out.getvalue()
        validate_output_size(result)
        return result, out_ext
