"""
Bot全体で使う設定値を環境変数から読み込むモジュール。
トークンなどの機密情報は必ず環境変数（.env）で管理し、コードには直書きしない。
"""
import os
import math
from typing import Optional
from dotenv import load_dotenv

# .env ファイルがあれば読み込む（本番では環境変数を直接設定してもOK）
load_dotenv()

# --- Botブランド情報 ---
BOT_NAME = "Convake"
BOT_NAME_JA = "こんばけ"
BOT_DISPLAY_NAME = "Convake"
BOT_PROFILE_BIO = "画像やファイルを変幻自在に化けさせる相棒Bot「Convake (こんばけ)」です！ /help でコマンド一覧を表示します。"


def _int_env(name: str, default: int, minimum: int = 1, maximum: Optional[int] = None) -> int:
    """環境変数の数値を安全に読み込む。異常値は起動時に明示的に拒否する。
    環境変数が「未設定」の場合だけでなく「空文字列が設定されている」場合もデフォルト値を使う
    （.envに `KEY=` とだけ書かれているケースで起動エラーになるのを防ぐ）。"""
    raw = os.getenv(name, str(default)).strip()
    if raw == "":
        raw = str(default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} は整数で指定してください。") from exc
    if value < minimum:
        raise RuntimeError(f"{name} は {minimum} 以上で指定してください。")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"{name} は {maximum} 以下で指定してください。")
    return value

# Discord Botトークン（必須）
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError(
        "環境変数 DISCORD_TOKEN が設定されていません。"
        ".env ファイルを作成するか、環境変数を設定してください。"
    )

# コマンド同期を特定のテストサーバーに即時反映したい場合はここにサーバーIDを設定（開発用、任意）
# グローバル同期は反映まで最大1時間ほどかかることがあるため、開発中はこちらの利用を推奨
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID", "").strip()
DEV_GUILD_ID = int(DEV_GUILD_ID) if DEV_GUILD_ID.isdigit() else None

# 保存機能（リアクション）で使う絵文字
SAVE_REACTION_EMOJI = os.getenv("SAVE_REACTION_EMOJI", "📌")

# /botstatus など管理系コマンドを管理者(サーバー管理権限を持つ人)のみに限定するか
# 管理者向けコマンド（/botstatus, /premium_*など）を実行できるユーザーID。
# このBotは個人運用のため、サーバーごとの「管理権限」ではなく、
# この特定のDiscordユーザーIDのみに実行を限定する。
# .envで上書きしない限り、下のデフォルト値（あなたのユーザーID）がそのまま使われる。
OWNER_ID = _int_env("OWNER_ID", 1536204304220950530, minimum=0)

# 一時ファイルの保存先
TEMP_DIR = os.getenv("TEMP_DIR", "temp")

# --- 入力・出力・リソース制限 ---
# Discordのプランやサーバーブースト状況にかかわらず、Bot側で独自に上限を設ける。
# 以下のデフォルト値は「空きメモリ400〜500MB程度の低スペック環境」を前提にした値。
# より余裕のあるサーバーに移行した場合は、必要に応じて.envで上書きして緩めてよい。
MAX_INPUT_BYTES = _int_env("MAX_INPUT_BYTES", 15 * 1024 * 1024)
MAX_OUTPUT_BYTES = _int_env("MAX_OUTPUT_BYTES", 15 * 1024 * 1024)
# 16,000,000px(16MP)は一般的なスマホ写真(多くは8〜12MP前後)を十分カバーしつつ、
# Pillowでのデコード時ピーク使用量(生バッファの3〜4倍)を約200〜250MBに抑える値。
MAX_IMAGE_PIXELS = _int_env("MAX_IMAGE_PIXELS", 16_000_000)
try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
except ImportError:
    pass
MAX_IMAGE_DIMENSION = _int_env("MAX_IMAGE_DIMENSION", 6_000, maximum=20_000)
MAX_IMAGE_FRAMES = _int_env("MAX_IMAGE_FRAMES", 50, maximum=1_000)
MAX_PDF_PAGES = _int_env("MAX_PDF_PAGES", 30)
# PDFの1ページあたりの上限。デフォルトdpi=200でのA4サイズが約4MPなので、
# かなり大きな用紙サイズにも対応できる余裕を持たせつつ1ページ分のメモリ使用量を抑える。
MAX_PDF_PAGE_PIXELS = _int_env("MAX_PDF_PAGE_PIXELS", 8_000_000)
MAX_TEXT_BYTES = _int_env("MAX_TEXT_BYTES", 5 * 1024 * 1024)
# CSV→XLSX変換はopenpyxlが全セルをメモリ上のオブジェクトとして保持するため、
# 「行数×列数」が大きいCSVは少ないファイルサイズでも大量のメモリを消費しうる
# (例: 10万行×256列 ≒ 2560万セルは、1セルあたり数百byteとしても数GB規模になりうる)。
# このため一般的な用途で困らない範囲まで大きく絞っている。
MAX_CSV_ROWS = _int_env("MAX_CSV_ROWS", 20_000, maximum=1_000_000)
MAX_CSV_COLUMNS = _int_env("MAX_CSV_COLUMNS", 50, maximum=16_384)
MAX_CSV_CELL_CHARS = _int_env("MAX_CSV_CELL_CHARS", 20_000, maximum=1_000_000)
MAX_MERGE_INPUT_BYTES = _int_env("MAX_MERGE_INPUT_BYTES", 30 * 1024 * 1024)
# /mergeで結合できる画像枚数の上限。
# Discordのスラッシュコマンドはユーザーごとに入力欄の数を変えられない（image1〜image5は誰にでも
# 表示される）ため、実際の制限は「送信後に枚数を数えてプレミアムか判定する」形で行う。
# image1〜image5の5枠が実装上の上限のため、PREMIUM側の値も5を超えないようにしている。
MAX_MERGE_IMAGES_FREE = _int_env("MAX_MERGE_IMAGES_FREE", 3, minimum=2, maximum=5)
MAX_MERGE_IMAGES_PREMIUM = _int_env("MAX_MERGE_IMAGES_PREMIUM", 5, minimum=2, maximum=5)
MAX_SCAN_TOTAL_BYTES = _int_env("MAX_SCAN_TOTAL_BYTES", 30 * 1024 * 1024)

# --- メモリ使用量対策 ---
# 重い処理（動画変換・背景除去）の同時実行数。同時に多く走るとメモリ使用量が積み上がるため制限する。
# 空きメモリが数百MB規模の環境では、2以上にするとピークメモリが単純倍増してOOMの危険が
# 大きく上がるため1を推奨。サーバーを増強した後に緩めるとよい。
MAX_CONCURRENT_HEAVY_TASKS = _int_env("MAX_CONCURRENT_HEAVY_TASKS", 1)
# Discordメッセージキャッシュの保持件数。Botはメッセージキャッシュに依存しない実装のため
# 既定で無効化(None)し、メモリ使用量を削減する
MAX_MESSAGE_CACHE = os.getenv("MAX_MESSAGE_CACHE", "").strip()
MAX_MESSAGE_CACHE = int(MAX_MESSAGE_CACHE) if MAX_MESSAGE_CACHE.isdigit() else None

# 日本語PDF作成用フォント（TXT->PDF変換で使用。無ければ英数字のみのフォールバック）
JP_FONT_PATH = os.getenv("JP_FONT_PATH", "fonts/NotoSansJP-Regular.ttf")

# --- プレミアム会員機能（手動確認＋コマンド登録方式） ---
# 個人間送金（PayPay等）は入金を自動検知するAPIが無いため、
# 「購入ボタンで支払いリンクを提示 → 本人が入金報告 → 管理者が確認して /premium_grant で登録」
# という半自動フローを前提にしている。期限管理（リマインドDM・自動剥奪）だけを自動化する。
# 会員判定はDiscordのロール（サーバーごと）ではなく、ユーザーID単位でutils/premium_store.pyが
# 直接管理する。これにより、Botが導入されているどのサーバーでも（DMでも）同じ人は
# プレミアムとして扱われる。ロールはあくまで登録時のサーバー内での見た目のバッジ用途（任意）。
PREMIUM_ROLE_ID = _int_env("PREMIUM_ROLE_ID", 0, minimum=0)  # 0=未設定（任意機能。サーバー内表示用バッジ程度の扱い）
PREMIUM_PRICE_JPY = _int_env("PREMIUM_PRICE_JPY", 300, minimum=0)
PAYPAY_LINK = os.getenv("PAYPAY_LINK", "").strip()
# 支払い報告の通知先チャンネル（未設定なら、Botのアプリ所有者にDMで通知する）
PREMIUM_ADMIN_NOTIFY_CHANNEL_ID = _int_env("PREMIUM_ADMIN_NOTIFY_CHANNEL_ID", 0, minimum=0)
# 期限の何日前にリマインドDMを送るか
PREMIUM_REMINDER_DAYS_BEFORE = _int_env("PREMIUM_REMINDER_DAYS_BEFORE", 3, minimum=1, maximum=30)
# 会員データの保存先（軽量JSONファイル。人数規模的にDBは不要と判断）
PREMIUM_STORE_PATH = os.getenv("PREMIUM_STORE_PATH", os.path.join("data", "premium_members.json"))
# ユーザーにプレミアム登録を案内する際に共通で表示する文言・招待リンク
SUPPORT_SERVER_INVITE_URL = os.getenv("SUPPORT_SERVER_INVITE_URL", "https://discord.gg/zy5KZct2Tu")
PREMIUM_PROMOTION_MESSAGE = f"[サポートサーバー]({SUPPORT_SERVER_INVITE_URL})にてプレミアム登録できます。"

# --- /link（画像・テキストのリンク化） ---
# 専用の保管用チャンネルにBotが投稿し、そのCDN URLを返す方式（外部ホスティング不使用）。
# 0（未設定）の場合、/linkは「未設定です」と案内するだけで動作しない。
LINK_STORAGE_CHANNEL_ID = _int_env("LINK_STORAGE_CHANNEL_ID", 0, minimum=0)
# textパラメータで直接渡せるテキストの最大文字数（Discordの文字列パラメータ上限は6000文字）
MAX_LINK_TEXT_CHARS = _int_env("MAX_LINK_TEXT_CHARS", 4000, minimum=1, maximum=6000)
# /link履歴の保存先。
# 履歴にはメッセージIDを記録しておき、Discordの署名付きCDNリンク(一定時間で期限切れ)が
# 切れても、メッセージを取得し直して新しい有効なリンクを再発行できるようにしている。
LINK_STORE_PATH = os.getenv("LINK_STORE_PATH", os.path.join("data", "link_history.json"))
# 履歴は無料会員にも提供し、「保存期間」と「保存件数」でプレミアムとの差をつける。
# 期限が来た履歴は、履歴の記録だけ取り除かれ再発行できなくなる（保管用チャンネルのメッセージは残す）。
# 保存期間は「作成した時点の会員種別」で確定する（後からプレミアムが切れても、
# すでに作った履歴が予告より早く消えることはない）。
LINK_RETENTION_DAYS_FREE = _int_env("LINK_RETENTION_DAYS_FREE", 30, minimum=1, maximum=3650)
LINK_RETENTION_DAYS_PREMIUM = _int_env("LINK_RETENTION_DAYS_PREMIUM", 180, minimum=1, maximum=3650)
LINK_HISTORY_MAX_FREE = _int_env("LINK_HISTORY_MAX_FREE", 5, minimum=1, maximum=25)
LINK_HISTORY_MAX_PREMIUM = _int_env("LINK_HISTORY_MAX_PREMIUM", 10, minimum=1, maximum=25)

# --- 動画<->GIF変換設定 ---
# 動画→GIF / GIF→MP4はffmpegを使うため他の変換より重く、低スペック環境では負荷が大きい。
# プレミアム会員限定機能として恒久的に固定している（無料開放トグルは廃止）。
MAX_VIDEO_DURATION_SECONDS = _int_env("MAX_VIDEO_DURATION_SECONDS", 15, maximum=600)
MAX_VIDEO_WIDTH = _int_env("MAX_VIDEO_WIDTH", 1280, maximum=8_000)
MAX_VIDEO_HEIGHT = _int_env("MAX_VIDEO_HEIGHT", 720, maximum=8_000)
GIF_FPS = _int_env("GIF_FPS", 10, maximum=60)
GIF_WIDTH = _int_env("GIF_WIDTH", 400, maximum=MAX_VIDEO_WIDTH)

# ユーザーは前回の処理が終わるまで次の重いコマンドを実行できない。
# サーバー単位では、受け付けたコマンド間に最低このぶんの秒数を空ける。
# 低スペック環境では、重い処理の間にメモリを回収する時間を確保する意味も兼ねて
# デフォルトの1秒より長めの3秒にしている。
SERVER_COOLDOWN_SECONDS = float(os.getenv("SERVER_COOLDOWN_SECONDS", "3"))
if not math.isfinite(SERVER_COOLDOWN_SECONDS) or SERVER_COOLDOWN_SECONDS < 0:
    raise RuntimeError("SERVER_COOLDOWN_SECONDS は0以上で指定してください。")

# プレミアム会員向けの短縮版クールダウン。判定は「そのコマンドを実行した本人」が
# プレミアムかどうかで行う（同じサーバー内の他のユーザーには影響しない）。
# 同時実行数の上限(MAX_CONCURRENT_HEAVY_TASKS)は変えないため、Bot全体の負荷は増えない。
PREMIUM_SERVER_COOLDOWN_SECONDS = float(os.getenv("PREMIUM_SERVER_COOLDOWN_SECONDS", "1") or "1")
if not math.isfinite(PREMIUM_SERVER_COOLDOWN_SECONDS) or PREMIUM_SERVER_COOLDOWN_SECONDS < 0:
    raise RuntimeError("PREMIUM_SERVER_COOLDOWN_SECONDS は0以上で指定してください。")


# --- 添付ファイルの危険拡張子/ウイルスチェック設定（手動コマンド /scan 用） ---
DEFAULT_DANGEROUS_EXTENSIONS = (
    "exe,bat,cmd,com,cpl,scr,vbs,vbe,js,jse,wsf,wsh,msi,msc,jar,"
    "ps1,ps1xml,psc1,reg,dll,sys,apk,app,pif,gadget,hta,lnk,sh,bin"
)
DANGEROUS_EXTENSIONS = set(
    e.strip().lower()
    for e in os.getenv("DANGEROUS_EXTENSIONS", DEFAULT_DANGEROUS_EXTENSIONS).split(",")
    if e.strip()
)

# VirusTotal API (任意)。設定するとハッシュ照合による既知マルウェア検出を行う。
# https://www.virustotal.com/gui/my-apikey で無料取得可能（レート制限に注意）
VIRUSTOTAL_API_KEY = os.getenv("VIRUSTOTAL_API_KEY", "").strip()


