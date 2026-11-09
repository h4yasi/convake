"""
/help コマンド（ページ切り替え式一覧 ＋ 個別コマンド詳細）。
全コマンドをカテゴリ別のページに分けて表示し、ボタンやセレクトメニューで
直感的にページを切り替えられる。

コマンド名の表示は、タップ1つでそのコマンドを実行できるDiscordのコマンドメンション
（</名前:ID>）を使う。IDはBot起動時のコマンド同期後にしか分からないため、
utils.command_registry から都度取得して組み立てている（同期前に呼ばれた場合は
`/名前` のプレーンテキストにフォールバックする）。
"""
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    SAVE_REACTION_EMOJI,
    LINK_RETENTION_DAYS_FREE,
    LINK_RETENTION_DAYS_PREMIUM,
    BOT_NAME_JA,
    BOT_DISPLAY_NAME,
)
try:
    from cogs.link import format_days
except Exception:
    def format_days(days: int) -> str:
        return f"{days // 30}ヶ月" if days % 30 == 0 else f"{days}日"
from utils import command_registry, status_tracker

CommandName = Literal[
    "convert",
    "compress",
    "merge",
    "rotate",
    "flip",
    "watermark",
    "emoji",
    "sticker",
    "avatar_crop",
    "strip_exif",
    "palette",
    "link",
    "link_list",
    "link_delete",
    "scan",
    "premium_settings",
    "admin",
]

# 各コマンドがどのヘルプページ（0始まり）に属するかのマッピング
COMMAND_PAGE_MAP: dict[str, int] = {
    "convert": 0,
    "compress": 0,
    "merge": 0,
    "rotate": 0,
    "flip": 1,
    "watermark": 1,
    "emoji": 1,
    "sticker": 1,
    "avatar_crop": 1,
    "palette": 1,
    "strip_exif": 2,
    "link": 2,
    "link_list": 2,
    "link_delete": 2,
    "scan": 2,
    "premium_settings": 3,
    "admin": 3,
}

# (ページタイトル, 絵文字, セレクトメニュー用ショート説明)
HELP_PAGE_META: list[tuple[str, str, str]] = [
    ("1. 変換・圧縮・結合・回転", "🔄", "/convert, /compress, /merge, /rotate"),
    ("2. 画像加工・絵文字・透かし・配色", "🎨", "/flip, /watermark, /emoji, /sticker, /avatar_crop, /palette"),
    ("3. リンク化・Exif削除・安全検査", "🔗", "/strip_exif, /link, /link_list, /link_delete, /scan"),
    ("4. 右クリック・便利機能・会員設定", "⚙️", "右クリックメニュー, 📌保存, /premium_settings, /admin"),
]

TOTAL_HELP_PAGES = len(HELP_PAGE_META)

# usage_args / examples_args は、コマンド名の部分を含めない（メンションに置き換えるため）。
COMMAND_DETAILS = {
    "convert": {
        "label": "ファイル変換",
        "usage_args": "file:<ファイル> target:<変換先>",
        "description": (
            "アップロードしたファイルを別の形式に変換します。\n"
            "`target` は選択式で以下から選べます: `png` `jpg` `webp` `bmp` `tiff` `ico` `pdf` `xlsx` `gif` `mp4`\n"
            "画像⇔画像の変換は **GIFを除く全形式が相互変換可能**です"
            "（GIFはアニメーション対応のため、動画⇔GIF変換として別枠で扱っています）。"
        ),
        "examples_args": [
            ("file:photo.jpg target:png", "画像をPNGに変換"),
            ("file:photo.png target:ico", "アイコン用のICOに変換（大きい画像は自動で256px以下に縮小）"),
            ("file:photo.png target:tiff", "TIFFに変換"),
            ("file:document.pdf target:png", "PDFの各ページをPNG画像に変換"),
            ("file:photo.png target:pdf", "画像をPDFに変換"),
            ("file:notes.txt target:pdf", "テキストをPDFに変換"),
            ("file:data.csv target:xlsx", "CSVをExcelに変換"),
            ("file:clip.mp4 target:gif", "動画をGIFに変換（プレミアム会員限定・15秒以内）"),
            ("file:anim.gif target:mp4", "GIFをMP4動画に変換（プレミアム会員限定）"),
        ],
    },
    "compress": {
        "label": "画像圧縮",
        "usage_args": "file:<画像> [quality:<1-95>] [max_dimension:<px>]",
        "description": (
            "画像を圧縮して軽量化します。`quality` を省略すると70で処理されます。\n"
            "`max_dimension` を指定すると、長辺がそのピクセル数を超えないようリサイズもします。"
        ),
        "examples_args": [
            ("file:photo.png", "デフォルト設定(品質70)で圧縮"),
            ("file:photo.png quality:50", "より強く圧縮"),
            ("file:photo.png quality:60 max_dimension:1280", "圧縮+リサイズ"),
        ],
    },
    "merge": {
        "label": "画像結合",
        "usage_args": "layout:<レイアウト> image1:<画像> image2:<画像> [image3] [image4] [image5] [columns:<列数>]",
        "description": (
            "複数の画像を1枚に結合します。`layout` は選択式で「縦に結合」「横に結合」「グリッド」から選べます。\n"
            "画像は image1〜image5 に直接添付してください（無料会員は最大3枚、プレミアム会員は最大5枚。image1とimage2は必須）。\n"
            "`columns` は `layout` が「グリッド」のときのみ有効で、省略すると自動で列数が決まります。"
        ),
        "examples_args": [
            ("layout:縦に結合 image1:a.png image2:b.png", "2枚を縦に結合"),
            ("layout:グリッド columns:2 image1:a.png image2:b.png image3:c.png image4:d.png", "4枚を2列グリッドで結合（プレミアム会員限定の4枚目）"),
        ],
    },
    "link": {
        "label": "画像・動画・テキストのリンク化",
        "usage_args": "[media:<画像/動画>] [text:<テキスト>] [file:<ファイル>]",
        "description": (
            "画像・動画やテキストをDiscordのCDN直リンクに変換します。`media`・`text`・`file` のいずれか1つを指定してください。\n"
            "`text` は直接入力した短いテキスト用（文字数に上限あり、.txtとして保存されます）、"
            "`file` は既存のテキスト系ファイル（txt/md/csv/json/log）用です。\n"
            "※ DiscordのCDNリンクは仕様上、一定時間（目安: 約1日）で期限切れになります。"
            "期限が切れても、履歴（/link_list）から新しいリンクを再発行できます。"
        ),
        "examples_args": [
            ("media:photo.png", "画像をリンク化"),
            ("media:clip.mp4", "動画をリンク化"),
            ("text:こんにちは", "短いテキストをリンク化"),
            ("file:notes.txt", "テキストファイルをリンク化"),
        ],
    },
    "link_list": {
        "label": "リンク履歴・再発行",
        "usage_args": "",
        "description": (
            "これまで /link で作成したリンクの履歴を、新しい有効なリンクで再発行して表示します（本人にだけ見えます）。\n"
            "期限切れになったリンクも、ここから新しいものを取得できます。\n"
            f"履歴の保存期間は無料会員が{format_days(LINK_RETENTION_DAYS_FREE)}、"
            f"プレミアム会員が{format_days(LINK_RETENTION_DAYS_PREMIUM)}で、期限が来ると履歴から外れて再発行できなくなります。"
        ),
        "examples_args": [("", "履歴を一覧表示")],
    },
    "link_delete": {
        "label": "リンク削除",
        "usage_args": "link:<履歴から選択>",
        "description": "作成したリンクを、保管データごと完全に削除します。削除したリンクは以後使えなくなります。\n履歴の期限が過ぎても保管データ自体は残るため、消したい場合はこのコマンドで削除してください。",
        "examples_args": [("link:photo.png（10/13 07:01）", "選択したリンクを削除")],
    },
    "rotate": {
        "label": "画像回転",
        "usage_args": "image:<画像> angle:<90° 右/180°/90° 左>",
        "description": "画像を90度単位で回転します（画質劣化なし）。結果はそのままファイルで返されます。",
        "examples_args": [
            ("image:photo.jpg angle:90° 右", "画像を時計回りに90度回転"),
        ],
    },
    "flip": {
        "label": "画像反転（左右・上下）",
        "usage_args": "file:<画像> [direction:<horizontal/vertical>]",
        "description": (
            "画像を左右反転（水平ミラー）または上下反転（垂直フリップ）します。\n"
            "`direction:horizontal` は左右反転（インカメラ自撮りの修正やイラストの左右反転チェックに便利）、"
            "`direction:vertical` は上下反転です（省略時は左右反転）。\n"
            "※ 画像付きメッセージを右クリック → アプリ → 「画像を左右反転」でもワンタップで実行できます。"
        ),
        "examples_args": [
            ("file:selfie.jpg", "画像を左右反転（鏡像化）"),
            ("file:art.png direction:vertical", "画像を上下反転"),
        ],
    },
    "watermark": {
        "label": "ウォーターマーク（透かし・サイン）",
        "usage_args": "file:<画像> text:<文字> [position:<位置>] [opacity:<透明度>]",
        "description": (
            "画像にユーザー名やID、SAMPLE文字などの透かしを自動合成します。\n"
            "`position` は「右下サイン（bottom_right）」「中央斜め（center）」「全面パターン（repeat・無断転載防止）」から選べます。\n"
            "`opacity` は透明度（light: 控えめ / medium: 標準 / strong: くっきり）を指定できます。"
        ),
        "examples_args": [
            ("file:art.png text:@my_twitter_id", "右下に作者サインを薄く合成"),
            ("file:photo.jpg text:SAMPLE position:center opacity:strong", "中央に斜めのSAMPLE透かしを配置"),
            ("file:illustration.png text:DO NOT REPOST position:repeat opacity:light", "無断転載防止の格子パターン透かしを全面に合成"),
        ],
    },
    "emoji": {
        "label": "Discord絵文字化",
        "usage_args": "file:<画像> [fit:<crop/pad>]",
        "description": (
            "画像をDiscordのカスタム絵文字規格（128x128px・最大128KB）に自動最適化します。\n"
            "`fit:crop` は中央正方形切り抜き、`fit:pad` は比率を維持したまま透明な余白を追加します。"
        ),
        "examples_args": [
            ("file:photo.png", "中央を正方形に切り抜いて128KB以内の絵文字に変換"),
            ("file:icon.png fit:pad", "透明余白を加えて全体が収まる絵文字に変換"),
        ],
    },
    "sticker": {
        "label": "Discordスタンプ化",
        "usage_args": "file:<画像> [fit:<crop/pad>]",
        "description": "画像をDiscordのスタンプ規格（320x320px・最大512KB）に自動最適化します。",
        "examples_args": [
            ("file:illustration.png", "320x320pxのスタンプ用PNGに変換"),
        ],
    },
    "avatar_crop": {
        "label": "円形アバター切り抜き",
        "usage_args": "file:<画像>",
        "description": "四角い画像をDiscordアイコン用に円形に切り抜き、四隅を透明にしたPNGを出力します。",
        "examples_args": [
            ("file:profile.jpg", "円形に切り抜いた透過アイコンPNGを作成"),
        ],
    },
    "strip_exif": {
        "label": "Exif/位置情報抹消",
        "usage_args": "file:<画像>",
        "description": "画像に含まれるGPS位置情報（撮影場所の緯度経度）、撮影日時、カメラ端末名などのExifメタデータを完全抹消します。",
        "examples_args": [
            ("file:photo.jpg", "安全に投稿できるようメタデータをすべて消去"),
        ],
    },
    "palette": {
        "label": "カラーパレット抽出",
        "usage_args": "file:<画像> [colors:<3-8>]",
        "description": "イラストや写真から代表的なカラーを抽出し、見やすいカラーパレット画像とHEXカラーコード一覧を生成します。",
        "examples_args": [
            ("file:art.png colors:6", "代表色6色のパレットカードとカラーコードを生成"),
        ],
    },
    "scan": {
        "label": "ファイルの安全チェック",
        "usage_args": "file:<ファイル>",
        "description": (
            "ファイルの拡張子が危険なものでないか、また(設定されていれば)VirusTotalで既知のマルウェアでないかを"
            "チェックし、結果をチャンネルに表示します。**メッセージの削除などは一切行いません。**\n\n"
            "すでにチャンネルに投稿されているファイルをチェックしたい場合は、\n"
            "そのメッセージを右クリック(スマホは長押し) → **アプリ** → **添付ファイルを検査** でも同様のチェックができます。"
        ),
        "examples_args": [
            ("file:setup.exe", "アップロード前にファイルの安全性を確認"),
        ],
    },
    "admin": {
        "label": "開発者専用管理パネル",
        "usage_args": "",
        "description": (
            "Bot開発者（オーナー）専用の統合コントロールパネルを表示します。\n"
            "プレミアム付与・解除・会員一覧確認・購入パネル設置・Bot診断・メモリ解放(GC)などをボタン操作で行えます。"
        ),
        "examples_args": [
            ("", "管理コントロールパネルを開く（オーナーのみ実行可）"),
        ],
    },
    "premium_settings": {
        "label": "プレミアム会員向け設定",
        "usage_args": "ephemeral:<true/false>",
        "description": (
            "プレミアム会員本人が使える設定コマンドです（非会員は使用できません）。\n"
            "`ephemeral:true` にすると、以後すべてのコマンドの応答が自分にだけ見える形になります。\n"
            "`false`（デフォルト）にすると、非会員と同じく誰でも見える公開の応答に戻ります。\n"
            "また、DMでコマンドを使えるのはプレミアム会員のみです（非会員はDM不可）。"
        ),
        "examples_args": [
            ("ephemeral:true", "応答を自分にだけ見える形にする"),
            ("ephemeral:false", "公開に戻す"),
        ],
    },
}


def build_help_page_embed(page: int) -> discord.Embed:
    """指定されたページ番号（0始まり）に対応するヘルプEmbedを組み立てる。"""
    page = max(0, min(page, TOTAL_HELP_PAGES - 1))

    if page == 0:
        embed = discord.Embed(
            title=f"📖 {BOT_DISPLAY_NAME}({BOT_NAME_JA}) ヘルプ [1/4]：変換・圧縮・結合・回転",
            description=(
                f"画像や動画を変幻自在に変換し化けさせるbot、**{BOT_DISPLAY_NAME}({BOT_NAME_JA})**です！\n"
                "コマンド名をタップすると入力欄にセットされます。下のボタンやメニューでページを切り替えられます。"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="🔄 ファイル形式変換",
            value=(
                f"{command_registry.mention('convert')}\n"
                "ファイルを別形式に変換（画像⇔画像[GIF除く全形式] / PDF→PNG / 画像→PDF / TXT→PDF / CSV→XLSX / 動画→GIF・GIF→MP4[プレミアム限定]）"
            ),
            inline=False,
        )
        embed.add_field(
            name="🗜️ 画像圧縮・軽量化",
            value=(
                f"{command_registry.mention('compress')}\n"
                "画質（1〜95）や長辺の最大ピクセル数を指定して画像を圧縮・軽量化"
            ),
            inline=False,
        )
        embed.add_field(
            name="🖼️ 複数画像の結合",
            value=(
                f"{command_registry.mention('merge')}\n"
                "複数画像を1枚に結合（縦/横/グリッド。無料は最大3枚、プレミアムは最大5枚）"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔄 画像回転",
            value=(
                f"{command_registry.mention('rotate')}\n"
                "画像を90°右 / 180° / 90°左に画質劣化なしで回転"
            ),
            inline=False,
        )
    elif page == 1:
        embed = discord.Embed(
            title=f"🎨 {BOT_DISPLAY_NAME}({BOT_NAME_JA}) ヘルプ [2/4]：画像加工・絵文字・透かし・配色",
            description="イラスト制作やSNS投稿、Discordサーバーの絵文字・スタンプ作りに便利な加工機能です。",
            color=discord.Color.teal(),
        )
        embed.add_field(
            name="🪞 画像反転（左右・上下）",
            value=(
                f"{command_registry.mention('flip')}\n"
                "画像を左右反転（鏡像）または上下反転（右クリック「画像を左右反転」からも即実行可能）"
            ),
            inline=False,
        )
        embed.add_field(
            name="🏷️ ウォーターマーク（透かし・サイン）",
            value=(
                f"{command_registry.mention('watermark')}\n"
                "画像に作者サイン・SAMPLE・全面パターン（無断転載防止）などの文字透かしを合成"
            ),
            inline=False,
        )
        embed.add_field(
            name="✨ Discord絵文字化・スタンプ化",
            value=(
                f"{command_registry.mention('emoji')} / {command_registry.mention('sticker')}\n"
                "Discordカスタム絵文字（128x128px・128KB内）/ スタンプ（320x320px・512KB内）に自動最適化"
            ),
            inline=False,
        )
        embed.add_field(
            name="✂️ 円形アバター切り抜き",
            value=(
                f"{command_registry.mention('avatar_crop')}\n"
                "四角い画像をDiscordアイコン用に円形に切り抜き（背景透過PNG）"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎨 カラーパレット抽出",
            value=(
                f"{command_registry.mention('palette')}\n"
                "画像から主要カラー（3〜8色）を抽出し、カラーパレットカードとHEXコード一覧を生成"
            ),
            inline=False,
        )
    elif page == 2:
        embed = discord.Embed(
            title=f"🔗 {BOT_DISPLAY_NAME}({BOT_NAME_JA}) ヘルプ [3/4]：リンク化・Exif削除・安全検査",
            description="ファイルの共有リンク発行や、位置情報（Exif）の抹消、安全チェック機能です。",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="🛡️ Exif / 位置情報抹消",
            value=(
                f"{command_registry.mention('strip_exif')}\n"
                "写真の位置情報（GPS緯度・経度）や撮影機材・撮影日時などのExifメタデータを完全抹消"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔗 画像・動画・テキストのリンク化",
            value=(
                f"{command_registry.mention('link')}\n"
                "画像・動画・テキストをDiscordのCDN直リンクに変換"
            ),
            inline=False,
        )
        embed.add_field(
            name="🗂️ リンク履歴・再発行・削除",
            value=(
                f"{command_registry.mention('link_list')} / {command_registry.mention('link_delete')}\n"
                "作成したリンクの履歴確認・再発行・削除（履歴の保存期間はプレミアム会員の方が長い）"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔍 ファイルの安全チェック",
            value=(
                f"{command_registry.mention('scan')}\n"
                "ファイルの危険拡張子・既知マルウェアを手動チェック（メッセージ削除なし）"
            ),
            inline=False,
        )
    else:
        embed = discord.Embed(
            title=f"⚙️ {BOT_DISPLAY_NAME}({BOT_NAME_JA}) ヘルプ [4/4]：右クリック・便利機能・設定",
            description="コマンドを打たずに使える右クリックメニューやリアクション保存、会員設定・管理機能です。",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="🖱️ 右クリック（コンテキストメニュー）",
            value=(
                "メッセージを右クリック（スマホは長押し）→ **アプリ** から\n"
                "「画像を左右反転」「絵文字用に変換」「Exif情報を削除」「添付ファイルを検査」を直接実行できます。"
            ),
            inline=False,
        )
        embed.add_field(
            name=f"📌 擬似保存機能（リアクション: {SAVE_REACTION_EMOJI}）",
            value=(
                f"メッセージに {SAVE_REACTION_EMOJI} のリアクションを付けると、"
                "リアクションした人のDMにそのメッセージへのリンクが届きます（コマンドではなく自動機能です）。"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️ プレミアム会員向け設定",
            value=(
                f"{command_registry.mention('premium_settings')}\n"
                "コマンドの応答を自分にだけ見える形（ephemeral）にするか切り替え可能（DM利用も可）"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ 開発者専用管理パネル",
            value=(
                f"{command_registry.mention('admin')}\n"
                "開発者専用統合パネル（プレミアム付与・解除・一覧・購入パネル設置・診断・メモリ解放）"
            ),
            inline=False,
        )

    embed.set_footer(
        text=f"{BOT_DISPLAY_NAME}({BOT_NAME_JA}) • ページ {page + 1}/{TOTAL_HELP_PAGES} • /help command:<コマンド名> で個別詳細を表示"
    )
    return embed


class HelpPaginatedView(discord.ui.View):
    """ページ切り替え用のボタン＆カテゴリ選択メニューを備えた /help ビュー。"""

    def __init__(self, author_id: int, page: int = 0):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.page = max(0, min(page, TOTAL_HELP_PAGES - 1))
        self.interaction: Optional[discord.Interaction] = None
        self._sync_ui_state()

    def _sync_ui_state(self) -> None:
        self.prev_button.disabled = self.page <= 0
        self.page_indicator.label = f"ページ {self.page + 1} / {TOTAL_HELP_PAGES}"
        self.next_button.disabled = self.page >= TOTAL_HELP_PAGES - 1
        # インスタンス間でSelectOptionオブジェクトを共有しないよう毎回生成する
        self.category_select.options = [
            discord.SelectOption(
                label=title,
                value=str(idx),
                description=summary,
                emoji=emoji,
                default=(idx == self.page),
            )
            for idx, (title, emoji, summary) in enumerate(HELP_PAGE_META)
        ]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "このヘルプはコマンドを実行した本人のみ操作できます。ご自身で `/help` を実行してください。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="◀ 前へ", style=discord.ButtonStyle.secondary, row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self._sync_ui_state()
        await interaction.response.edit_message(
            embed=build_help_page_embed(self.page),
            view=self,
        )

    @discord.ui.button(label="ページ 1 / 4", style=discord.ButtonStyle.primary, disabled=True, row=0)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="次へ ▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < TOTAL_HELP_PAGES - 1:
            self.page += 1
        self._sync_ui_state()
        await interaction.response.edit_message(
            embed=build_help_page_embed(self.page),
            view=self,
        )

    @discord.ui.select(
        placeholder="📂 カテゴリを選んでページ移動...",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label=title,
                value=str(idx),
                description=summary,
                emoji=emoji,
            )
            for idx, (title, emoji, summary) in enumerate(HELP_PAGE_META)
        ],
        row=1,
    )
    async def category_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        try:
            selected = int(select.values[0])
        except (IndexError, ValueError):
            selected = 0
        self.page = max(0, min(selected, TOTAL_HELP_PAGES - 1))
        self._sync_ui_state()
        await interaction.response.edit_message(
            embed=build_help_page_embed(self.page),
            view=self,
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[attr-defined]
        if self.interaction is not None:
            try:
                await self.interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        status_tracker.record_error(f"/help view: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
            else:
                await interaction.response.send_message("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
        except (discord.HTTPException, discord.InteractionResponded):
            pass


class HelpDetailView(discord.ui.View):
    """個別コマンド詳細からページ一覧へ戻るボタンを提供するビュー。"""

    def __init__(self, author_id: int, back_page: int = 0):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.back_page = max(0, min(back_page, TOTAL_HELP_PAGES - 1))
        self.interaction: Optional[discord.Interaction] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "この操作はコマンドを実行した本人のみ行えます。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="📖 コマンド一覧（ページ表示）に戻る", style=discord.ButtonStyle.secondary)
    async def back_to_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = HelpPaginatedView(author_id=self.author_id, page=self.back_page)
        view.interaction = interaction
        self.stop()
        await interaction.response.edit_message(
            embed=build_help_page_embed(self.back_page),
            view=view,
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore[attr-defined]
        if self.interaction is not None:
            try:
                await self.interaction.edit_original_response(view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        status_tracker.record_error(f"/help detail view: {error}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
            else:
                await interaction.response.send_message("処理中にエラーが発生しました。時間を置いて再試行してください。", ephemeral=True)
        except (discord.HTTPException, discord.InteractionResponded):
            pass


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Botのコマンド一覧（ページ切り替え式）・詳しい使い方を表示します")
    @app_commands.describe(
        page="最初に開くページ（省略時は1ページ目を表示）",
        command="詳しい使い方を見たいコマンド（指定すると個別詳細を表示）",
    )
    @app_commands.choices(
        page=[
            app_commands.Choice(name="1ページ目: 変換・圧縮・結合・回転", value=1),
            app_commands.Choice(name="2ページ目: 画像加工・絵文字・透かし・配色", value=2),
            app_commands.Choice(name="3ページ目: リンク化・Exif削除・安全検査", value=3),
            app_commands.Choice(name="4ページ目: 右クリック・便利機能・会員設定", value=4),
        ]
    )
    async def help_command(
        self,
        interaction: discord.Interaction,
        page: Optional[app_commands.Choice[int]] = None,
        command: Optional[CommandName] = None,
    ):
        if command is not None:
            info = COMMAND_DETAILS[command]
            cmd_mention = command_registry.mention(command)
            embed = discord.Embed(
                title=f"/{command} — {info['label']}",
                description=info["description"],
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"{BOT_DISPLAY_NAME}({BOT_NAME_JA}) • 下のボタンまたは /help で一覧に戻る")
            embed.add_field(name="使い方", value=f"{cmd_mention} {info['usage_args']}".strip(), inline=False)
            examples = [
                f"{cmd_mention} {args} — {desc}" if desc else f"{cmd_mention} {args}"
                for args, desc in info["examples_args"]
            ]
            embed.add_field(name="使用例", value="\n".join(f"• {e}".strip() for e in examples), inline=False)
            back_page = COMMAND_PAGE_MAP.get(command, 0)
            view = HelpDetailView(author_id=interaction.user.id, back_page=back_page)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            view.interaction = interaction
            return

        initial_page = (page.value - 1) if page is not None else 0
        view = HelpPaginatedView(author_id=interaction.user.id, page=initial_page)
        await interaction.response.send_message(
            embed=build_help_page_embed(initial_page),
            view=view,
            ephemeral=True,
        )
        view.interaction = interaction


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
