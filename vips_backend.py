"""
libvips (pyvips) を使った高速・低メモリな画像処理バックエンド。

【なぜ使うか】
実測(6000x4000pxの画像)で、単純なフォーマット変換ではメモリ使用量が約1/3、
サムネイル生成ではshrink-on-load（デコード時点で縮小するテクニック）により
フルサイズを一度もメモリに展開せずに処理できるため、Pillowよりも効率的。

【なぜPillowを完全に置き換えないか】
- libvipsはOS側に本体(libvips)のインストールが必要で、pipだけでは動かない
  （例: Ubuntu/Debianなら `sudo apt install libvips`）。ホスティング環境によっては
  インストールできない/されていない可能性がある。
- ICO形式の書き出しに対応していない（読み込みは可能）。

そのため「使えるときだけ使う」ハイブリッド構成にしている。
このモジュールのimport自体は、pyvips/libvipsが無い環境でも失敗しないようにしてあり、
VIPS_AVAILABLE フラグで呼び出し側がPillowにフォールバックできるようにしている。
"""
import logging
from typing import Optional

logger = logging.getLogger("discord_bot")

try:
    import pyvips

    # importだけでなく実際に呼び出せるかも確認する。
    # (pyvipsパッケージは入っていても、OS側のlibvips本体が無いと
    #  ここで OSError / pyvips.error.Error になることがある)
    pyvips.Image.black(1, 1)
    VIPS_AVAILABLE = True
    _init_error = None
except Exception as e:  # ImportError, OSError, pyvips.error.Error など
    pyvips = None
    VIPS_AVAILABLE = False
    _init_error = e

if VIPS_AVAILABLE:
    logger.info("libvips(pyvips)が利用可能です。画像変換・圧縮を高速化します。")
else:
    logger.info(
        "libvips(pyvips)は利用できないため、Pillowで処理します（%s）。"
        "高速化したい場合はOSに libvips をインストールしてください。",
        _init_error,
    )


# vips形式名とファイル拡張子(write_to_bufferに渡すサフィックス)のマッピング。
# ICOはvipsが書き出しに対応していないため含めない（呼び出し側でPillowにフォールバックする）。
VIPS_SAVE_SUFFIX = {
    "png": ".png",
    "jpg": ".jpg",
    "jpeg": ".jpg",
    "webp": ".webp",
    "bmp": ".bmp",
    "tiff": ".tiff",
    "tif": ".tiff",
}


class VipsUnavailableError(Exception):
    """この環境/フォーマットではlibvipsが使えないことを示す（呼び出し側でPillowにフォールバックする）"""


def vips_convert_image(data: bytes, target_ext: str) -> bytes:
    """
    libvipsで画像フォーマットを変換する。
    Pillow版と出力サイズが概ね一致するよう、JPEG保存時は4:2:0クロマサブサンプリングを明示している
    （指定しないとvipsは高品質側=4:4:4を選び、同じQ値でもファイルサイズが倍近くになることがある）。
    """
    if not VIPS_AVAILABLE:
        raise VipsUnavailableError("pyvips/libvipsが利用できません")

    suffix = VIPS_SAVE_SUFFIX.get(target_ext.lower())
    if suffix is None:
        raise VipsUnavailableError(f"vipsはこのフォーマットの書き出しに非対応です: {target_ext}")

    img = pyvips.Image.new_from_buffer(data, "", access="sequential")

    if suffix == ".jpg":
        if img.hasalpha():
            # JPEGは透過を扱えないため白背景に合成する（Pillow版のRGB変換に相当）
            img = img.flatten(background=[255, 255, 255])
        return img.write_to_buffer(suffix, Q=92, subsample_mode="on")

    if suffix == ".bmp":
        if img.hasalpha():
            img = img.flatten(background=[255, 255, 255])
        return img.write_to_buffer(suffix)

    if suffix == ".webp":
        return img.write_to_buffer(suffix, Q=92)

    # png / tiff はアルファ付きのままで問題ない
    return img.write_to_buffer(suffix)


def vips_compress_image(data: bytes, quality: int, max_dimension: Optional[int] = None):
    """
    libvipsで画像を圧縮する。max_dimension指定時は thumbnail_buffer の shrink-on-load により、
    フルサイズを一度もデコードせずに縮小できるため、大きな画像ほどPillowよりメモリ効率が良い。
    戻り値: (圧縮後バイナリ, 拡張子)
    """
    if not VIPS_AVAILABLE:
        raise VipsUnavailableError("pyvips/libvipsが利用できません")

    if max_dimension:
        # size="down": Pillowのthumbnail()と同様、縮小のみ行い拡大はしない
        # no_rotate=True: 既存のPillow実装と挙動を合わせるためEXIFに基づく自動回転はしない
        img = pyvips.Image.thumbnail_buffer(
            data, max_dimension, height=max_dimension, size="down", no_rotate=True
        )
    else:
        img = pyvips.Image.new_from_buffer(data, "", access="sequential")

    if img.hasalpha():
        out = img.write_to_buffer(".webp", Q=quality)
        return out, "webp"
    else:
        out = img.write_to_buffer(".jpg", Q=quality, subsample_mode="on")
        return out, "jpg"
