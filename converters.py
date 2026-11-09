"""
ファイル変換ロジック本体。
discordに依存しない純粋な関数として実装し、テストや再利用をしやすくしている。

対応変換:
- 画像 -> 画像 (PNG / JPG / WebP / BMP / TIFF / ICO)
  ※GIF（アニメーション）は静止画変換の枠組みでは複雑になるため対象外。
    動画とGIF間の変換は utils/video.py（ffmpeg）側で別途サポートしている。
- PDF -> PNG (各ページを画像化)
- 画像 -> PDF
- TXT -> PDF
- CSV -> XLSX
"""
import csv
import io
import logging
import os
from typing import List, Optional, Tuple

from PIL import Image
try:
    import pymupdf as fitz  # PyMuPDF 1.24+
except ImportError:
    import fitz  # PyMuPDF 1.23系以前
import openpyxl
from openpyxl.utils import get_column_letter
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from config import (
    JP_FONT_PATH,
    MAX_CSV_CELL_CHARS,
    MAX_CSV_COLUMNS,
    MAX_CSV_ROWS,
    MAX_IMAGE_DIMENSION,
    MAX_IMAGE_FRAMES,
    MAX_IMAGE_PIXELS,
    MAX_OUTPUT_BYTES,
    MAX_PDF_PAGE_PIXELS,
    MAX_PDF_PAGES,
    MAX_TEXT_BYTES,
)
from utils.file_helpers import validate_data_size, validate_output_size
from utils.vips_backend import (
    VIPS_AVAILABLE,
    VIPS_SAVE_SUFFIX,
    VipsUnavailableError,
    vips_convert_image,
    vips_compress_image,
)

PIL_FORMAT_MAP = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
    "tif": "TIFF",
    "ico": "ICO",
}

# アルファチャンネル(透過)を保持できないフォーマット。変換時にRGBへ変換する必要がある。
NO_ALPHA_FORMATS = {"JPEG", "BMP"}

# ICOは仕様上256x256を超えるサイズを直接保存できないため、超える場合は縮小する
ICO_MAX_SIZE = 256


class ConversionError(Exception):
    """変換処理中に想定されるエラー"""


def _validate_image_data(data: bytes) -> None:
    """画像の展開前に、寸法・フレーム数によるリソース制限を確認する。"""
    validate_data_size(data)
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if (
                width < 1
                or height < 1
                or width > MAX_IMAGE_DIMENSION
                or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise ConversionError(
                    f"画像サイズが上限を超えています（最大{MAX_IMAGE_DIMENSION}px、"
                    f"{MAX_IMAGE_PIXELS:,}ピクセル）。"
                )
            if getattr(img, "n_frames", 1) > MAX_IMAGE_FRAMES:
                raise ConversionError(
                    f"画像のフレーム数が上限({MAX_IMAGE_FRAMES})を超えています。"
                )
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError("画像ファイルを読み込めませんでした。") from exc


def _convert_image_to_image_pillow(data: bytes, target_ext: str) -> bytes:
    """Pillowによる画像フォーマット変換（libvipsが使えない場合のフォールバック、またはICO専用）"""
    if target_ext not in PIL_FORMAT_MAP:
        raise ConversionError(f"未対応の画像フォーマットです: {target_ext}")

    with Image.open(io.BytesIO(data)) as img:
        pil_format = PIL_FORMAT_MAP[target_ext]

        # 透過を扱えないフォーマットに変換する場合はRGBに変換する
        if pil_format in NO_ALPHA_FORMATS and img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        # ICOは256x256を超えるサイズを直接保存できないため、超える場合は縮小する
        if pil_format == "ICO" and (img.width > ICO_MAX_SIZE or img.height > ICO_MAX_SIZE):
            img = img.copy()
            img.thumbnail((ICO_MAX_SIZE, ICO_MAX_SIZE), Image.LANCZOS)

        out = io.BytesIO()
        save_kwargs = {}
        if pil_format == "JPEG":
            save_kwargs["quality"] = 92
        img.save(out, format=pil_format, **save_kwargs)
        result = out.getvalue()
        validate_output_size(result)
        return result


def convert_image_to_image(data: bytes, target_ext: str) -> bytes:
    """
    画像を別の画像フォーマットに変換する（GIFは対象外。動画変換側を利用すること）。

    libvipsが利用可能かつ対応フォーマット（ICO以外）であれば、
    メモリ効率の良いlibvipsで処理する。ICOはlibvipsが書き出しに対応していないため、
    また、libvipsが利用できない環境ではPillowにフォールバックする。
    """
    target_ext = target_ext.lower()
    if target_ext not in PIL_FORMAT_MAP:
        raise ConversionError(f"未対応の画像フォーマットです: {target_ext}")
    _validate_image_data(data)

    if VIPS_AVAILABLE and target_ext in VIPS_SAVE_SUFFIX:
        try:
            result = vips_convert_image(data, target_ext)
            validate_output_size(result)
            return result
        except VipsUnavailableError:
            pass  # フォールバックしてPillowで処理する
        except Exception as e:
            # vips側で予期しない失敗をした場合もPillowで再試行する（できるだけ変換を成功させる）
            logging.getLogger("discord_bot").warning(
                "vipsでの画像変換に失敗したためPillowにフォールバックします: %s", e
            )

    return _convert_image_to_image_pillow(data, target_ext)


def convert_pdf_to_png(data: bytes, dpi: int = 200) -> List[Tuple[str, bytes]]:
    """PDFの各ページをPNG画像に変換する。戻り値は (ファイル名, PNGバイナリ) のリスト"""
    validate_data_size(data)
    results = []
    total_output_bytes = 0
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ConversionError("PDFの読み込みに失敗しました。") from e

    try:
        if len(doc) > MAX_PDF_PAGES:
            raise ConversionError(f"PDFは{MAX_PDF_PAGES}ページ以下にしてください。")

        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            width = max(1, int(page.rect.width * zoom))
            height = max(1, int(page.rect.height * zoom))
            if width * height > MAX_PDF_PAGE_PIXELS:
                raise ConversionError("PDFのページサイズが大きすぎます。")
            pix = page.get_pixmap(matrix=matrix)
            png_bytes = pix.tobytes("png")
            validate_output_size(png_bytes)
            total_output_bytes += len(png_bytes)
            if total_output_bytes > MAX_OUTPUT_BYTES:
                raise ConversionError("PDF全体の変換結果が大きすぎます。")
            filename = f"page_{page_index + 1:03d}.png"
            results.append((filename, png_bytes))
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError("PDFページの画像化に失敗しました。") from e
    finally:
        doc.close()
    return results


def convert_image_to_pdf(data: bytes) -> bytes:
    """画像をPDFに変換する（1画像1ページ）"""
    _validate_image_data(data)
    with Image.open(io.BytesIO(data)) as img:
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="PDF")
        result = out.getvalue()
        validate_output_size(result)
        return result


def convert_txt_to_pdf(data: bytes) -> bytes:
    """
    テキストファイルをPDFに変換する。
    日本語などUnicode文字を正しく表示するには TrueTypeフォント (JP_FONT_PATH) が必要。
    フォントが見つからない場合はASCII互換フォントにフォールバックする
    （非対応文字は "?" 等に置き換えられる可能性がある）。
    """
    validate_data_size(data)
    if len(data) > MAX_TEXT_BYTES:
        raise ConversionError("テキストファイルが大きすぎます。")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp932", errors="replace")  # 日本語Windows環境のtxt対策

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    font_path = JP_FONT_PATH
    if not os.path.exists(font_path):
        fallback_font = os.path.join(os.path.dirname(os.path.dirname(__file__)), JP_FONT_PATH)
        if os.path.exists(fallback_font):
            font_path = fallback_font

    use_unicode_font = os.path.exists(font_path)
    if use_unicode_font:
        try:
            pdf.add_font("JPFont", "", font_path)
        except TypeError:
            pdf.add_font("JPFont", "", font_path, uni=True)
        pdf.set_font("JPFont", size=12)
    else:
        pdf.set_font("Helvetica", size=12)
        # 非Latin1文字は落とす（文字化け防止のため置換）
        text = text.encode("latin-1", errors="replace").decode("latin-1")

    for line in text.split("\n"):
        if line == "":
            # fpdf2は空文字列をmulti_cellに渡すとエラーになるため、空行は改行のみ行う
            pdf.ln(8)
        else:
            # new_x/new_y を明示しないと、書き込み後カーソルがセル右端に残り
            # 次の行の描画スペース計算が壊れる（右端に近づくほど幅が足りなくなりエラーになる）ため必ず指定する
            pdf.multi_cell(0, 8, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    try:
        out = pdf.output()
    except TypeError:
        out = pdf.output(dest="S")
    if isinstance(out, (bytearray, bytes)):
        result = bytes(out)
    else:
        result = str(out).encode("latin-1")
    validate_output_size(result)
    return result


def convert_csv_to_xlsx(data: bytes) -> bytes:
    """CSVファイルをXLSXに変換する"""
    validate_data_size(data)
    if len(data) > MAX_TEXT_BYTES:
        raise ConversionError("CSVファイルが大きすぎます。")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp932", errors="replace")

    csv.field_size_limit(MAX_CSV_CELL_CHARS)
    reader = csv.reader(io.StringIO(text))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    max_col = 0
    for row_idx, row in enumerate(reader, start=1):
        if row_idx > MAX_CSV_ROWS:
            raise ConversionError(f"CSVは{MAX_CSV_ROWS:,}行以下にしてください。")
        if len(row) > MAX_CSV_COLUMNS:
            raise ConversionError(f"CSVは{MAX_CSV_COLUMNS}列以下にしてください。")
        for col_idx, value in enumerate(row, start=1):
            if len(value) > MAX_CSV_CELL_CHARS:
                raise ConversionError("CSVのセルが長すぎます。")
            # Excelで数式として実行されないよう、外部入力は常に文字列化する。
            safe_value = f"'{value}" if value.startswith(("=", "+", "-", "@")) else value
            ws.cell(row=row_idx, column=col_idx, value=safe_value)
        max_col = max(max_col, len(row))

    # 簡易的な列幅自動調整
    for col_idx in range(1, max_col + 1):
        letter = get_column_letter(col_idx)
        max_len = max(
            (len(str(ws.cell(row=r, column=col_idx).value or "")) for r in range(1, ws.max_row + 1)),
            default=10,
        )
        ws.column_dimensions[letter].width = min(max(max_len + 2, 8), 50)

    out = io.BytesIO()
    wb.save(out)
    result = out.getvalue()
    validate_output_size(result)
    return result


def compress_image(data: bytes, quality: int = 70, max_dimension: Optional[int] = None) -> Tuple[bytes, str]:
    """
    画像を圧縮する。
    - quality: JPEG/WebP品質 (1-95)
    - max_dimension: 指定した場合、長辺がこのピクセル数を超えないようリサイズする
    戻り値: (圧縮後バイナリ, 拡張子)
    透過情報を持つ画像はWebPで、それ以外はJPEGで圧縮する（圧縮率を優先）。

    libvipsが利用可能な場合はそちらを使う。特にmax_dimension指定時は
    shrink-on-load（デコード時点で縮小する）によりフルサイズを展開せずに済むため、
    大きな画像ほどPillowよりメモリ効率が良い。利用できない場合はPillowにフォールバックする。
    """
    quality = 70 if quality is None else max(1, min(95, int(quality)))
    _validate_image_data(data)

    if VIPS_AVAILABLE:
        try:
            result = vips_compress_image(data, quality, max_dimension)
            validate_output_size(result[0])
            return result
        except VipsUnavailableError:
            pass
        except Exception as e:
            logging.getLogger("discord_bot").warning(
                "vipsでの画像圧縮に失敗したためPillowにフォールバックします: %s", e
            )

    with Image.open(io.BytesIO(data)) as img:
        if max_dimension:
            w, h = img.size
            longest = max(w, h)
            if longest > max_dimension:
                scale = max_dimension / longest
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        out = io.BytesIO()
        if has_alpha:
            img = img.convert("RGBA")
            img.save(out, format="WEBP", quality=quality, method=6)
            ext = "webp"
        else:
            img = img.convert("RGB")
            img.save(out, format="JPEG", quality=quality, optimize=True)
            ext = "jpg"
        result = out.getvalue()
        validate_output_size(result)
        return result, ext
