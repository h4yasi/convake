# Convake - Discord ファイル変換・画像ユーティリティBot

Python (discord.py) で実装した、画像や動画、各種ファイルをサクッと「化けさせる」多機能Discord Bot「**Convake**」です。
Bot自身の自称・愛称は「**こんばけ**」。「画像や動画編集で別のアプリやブラウザに飛ぶ小さな面倒を減らす」をモットーに、全機能をスラッシュコマンドと右クリックメニューでDiscord上で完結させます。

### 🏷️ 推奨プロフィール設定
- **Bot名 (Username)**: `Convake`
- **サーバー表示名 (Display Name)**: `Convake`
- **自己紹介 (About Me)**:
  ```text
  画像やファイルを変幻自在に化けさせる相棒Bot「Convake (こんばけ)」です！
  こんばけがDiscordのチャット内でそのまま完結する軽量・高速な編集ツールを提供します。
  /help で全コマンドの一覧と使い方を確認できます。
  ```
- **ステータス表示 (Custom Status)**:
  `画像をサクッと化けさせる相棒 | /help`

## コマンド一覧

| コマンド | 説明 |
|---|---|
| `/convert` | ファイル変換（画像⇔画像[GIF除く全形式:PNG/JPG/WebP/BMP/TIFF/ICO] / PDF→PNG / 画像→PDF / TXT→PDF / CSV→XLSX / 動画→GIF / GIF→MP4） |
| `/compress` | 画像圧縮 |
| `/merge` | 複数画像を1枚に結合（縦/横/グリッド、列数指定可） |
| `/rotate` | 画像を90度単位で回転（90°右 / 180° / 90°左） |
| `/flip` | 画像を左右反転（鏡像）または上下反転（右クリックからも実行可） |
| `/watermark` | 画像に作者サイン・SAMPLE・全面パターン（無断転載防止）などの文字透かしを合成 |
| `/emoji` | Discordカスタム絵文字規格（128x128px・最大128KB）に自動最適化 |
| `/sticker` | Discordスタンプ規格（320x320px・最大512KB）に自動最適化 |
| `/avatar_crop` | 四角い画像をDiscordアイコン用に円形に切り抜き（背景透過PNG） |
| `/strip_exif` | 写真から位置情報（GPS）・撮影日時などのExifメタデータを完全抹消 |
| `/palette` | 画像から主要カラーを抽出し、カラーパレットカードとHEXコード一覧を生成 |
| `/link` / `/link_list` / `/link_delete` | 画像・動画・テキストのCDN直リンク化と履歴管理・再発行・削除 |
| `/scan` | ファイルの危険拡張子・既知マルウェアを手動チェック（警告表示のみ、削除なし） |
| （右クリック）アプリメニュー | 「画像を左右反転」「絵文字用に変換」「Exif情報を削除」「添付ファイルを検査」 |
| `/premium_settings` | プレミアム会員向け設定（応答の非公開設定など） |
| `/admin` | 開発者専用統合パネル（プレミアム付与・解除・一覧・購入パネル設置・診断・メモリ解放(GC)） |
| `/help` | ページ切り替え式のコマンド一覧（4ページ）・詳しい使い方を表示 |
| リアクション（📌等） | リアクションした人のDMにメッセージリンクを送信する擬似保存機能 |

`/scan`および「添付ファイルを検査」は**手動実行のみ**で、自動監視や自動削除は行いません（結果をチャンネルに表示するだけです）。

## 利用制限

- ユーザーは、前回の変換・圧縮・結合・スキャン処理が完了するまで、次の対象コマンドを実行できません。
- 同一サーバーでは、コマンドの受付間隔をデフォルトで1秒空けます（`SERVER_COOLDOWN_SECONDS`で変更可能）。
- 入力サイズ、出力サイズ、画像ピクセル数、PDFページ数、CSV行数などにも上限があります。詳細は`.env.example`を確認してください。

> 以前あったチャンネル内画像の一括ダウンロード機能(`/download_channel`)、背景除去機能(`/removebg`)は削除しました。
> 背景除去は人物以外の被写体では誤検出が起きやすく判定精度に懸念があったこと、また依存ライブラリ(rembg)が
> 重くメモリ消費が大きかったことから廃止し、今後はファイル変換・画像編集機能の拡充に注力していく方針です。

---

## セットアップ

### 1. Discord Developer Portalでの準備
1. https://discord.com/developers/applications で New Application → Bot を作成
2. **Bot** タブで **MESSAGE CONTENT INTENT** を **ON** にする（リアクション保存機能のメッセージプレビュー表示に使用）
3. トークンをコピー（**絶対に公開しないこと**）
4. OAuth2 → URL Generator で `bot` と `applications.commands` の**両方の**スコープを選び、
   以下の権限を選んでサーバーに招待
   - Read Messages/View Channels, Send Messages, Attach Files, Read Message History, Add Reactions, Use Application Commands

### 2. 環境構築
```bash
python -m venv venv
source venv/bin/activate  # Windowsは venv\Scripts\activate
pip install -r requirements.txt
```

**ffmpegのインストール（動画⇔GIF変換に必須、pipには含まれません）**
```bash
# Ubuntu/Debian
sudo apt install ffmpeg
# macOS (Homebrew)
brew install ffmpeg
# Windows: https://www.gyan.dev/ffmpeg/builds/ からダウンロードしPATHに追加
```

**libvipsのインストール（`/convert`・`/compress`の高速化、任意）**
`pip install -r requirements.txt` で `pyvips` はインストールされますが、これはあくまで
OS側の`libvips`本体を呼び出すためのバインディングです。**`libvips`本体が無いと自動的にPillowにフォールバック**
するため、無くてもBotは正常に動作しますが、あると特に大きな画像の変換・圧縮でメモリ使用量・速度が改善します
（実測: 単純な形式変換でメモリ使用量が約1/3、サムネイル生成でも同程度以上の改善）。
```bash
# Ubuntu/Debian
sudo apt install libvips
# macOS (Homebrew)
brew install vips
# Windows: https://www.libvips.org/install.html を参照
```
インストール後、Bot起動時のログに `libvips(pyvips)が利用可能です` と出れば有効になっています。
出ない場合（`libvips(pyvips)は利用できないため、Pillowで処理します`）でも機能面の違いはなく、
処理速度・メモリ効率のみの違いです。ICO形式への変換は仕様上libvipsが非対応のため常にPillowで処理します。

### 3. 環境変数の設定
`.env.example` を `.env` にコピーして編集します（`.env` は `.gitignore` に追加してください）。

```bash
cp .env.example .env
```

開発中は `.env` の `DEV_GUILD_ID` にテストサーバーのIDを設定すると、
スラッシュコマンドが**即座に**そのサーバーへ反映されます（未設定＝グローバル同期の場合、反映まで最大1時間程度かかることがあります）。

### 4. （任意）TXT→PDF変換で日本語を使う場合
`fpdf2` の組み込みフォントは日本語（非Latin-1文字）に対応していません。
[Noto Sans JP](https://fonts.google.com/noto/specimen/Noto+Sans+JP) 等のTTFフォントを
`fonts/NotoSansJP-Regular.ttf` として配置してください（`.env` の `JP_FONT_PATH` で変更可）。
未配置でも動作しますが、日本語部分は `?` に置換されます。

### 5. 起動
```bash
python main.py
```
ログイン失敗時（レート制限など）は自動的に指数バックオフで再試行します
（詳細は末尾の「トラブルシューティング」参照）。

---

## コマンド詳細

### `/convert file:<ファイル> target:<変換先>`
- `target` はドロップダウンで選択: `png` `jpg` `webp` `bmp` `tiff` `ico` `pdf` `xlsx` `gif` `mp4`
- **画像⇔画像の変換はGIFを除く全形式（PNG/JPG/WebP/BMP/TIFF/ICO）が相互変換可能**です
  （6形式 × 6形式 = 全36通りの組み合わせで動作確認済み）。
  GIFはアニメーションを含み静止画変換の枠組みでは扱いが複雑になるため対象外にしており、
  動画との相互変換（下記）でのみサポートしています。
- 例:
  - `/convert file:photo.jpg target:png` → PNGに変換
  - `/convert file:photo.png target:tiff` → TIFFに変換
  - `/convert file:photo.png target:ico` → アイコン用ICOに変換（256pxを超える画像は自動縮小）
  - `/convert file:document.pdf target:png` → 各ページをPNG画像に変換（10枚ずつ分けて送信）
  - `/convert file:photo.png target:pdf` → PDFに変換
  - `/convert file:notes.txt target:pdf` → PDFに変換
  - `/convert file:data.csv target:xlsx` → Excelに変換
  - `/convert file:clip.mp4 target:gif` → GIFに変換（`MAX_VIDEO_DURATION_SECONDS`秒を超える動画は拒否、ffmpegによる高画質パレット変換）
  - `/convert file:anim.gif target:mp4` → MP4動画に変換（ffmpeg使用）

### `/compress file:<画像> [quality] [max_dimension]`
- `quality`: 1-95（省略時70、低いほど圧縮率が高い）
- `max_dimension`: 指定すると長辺がそのピクセル数を超えないようリサイズ
- 例: `/compress file:photo.png quality:60 max_dimension:1280`

### `/merge layout:<レイアウト> image1:<画像> image2:<画像> [image3] [image4] [image5] [columns]`
- `layout` はドロップダウンで選択: `vertical`(縦) / `horizontal`(横) / `grid`(グリッド)
- スラッシュコマンドの仕様上、結合したい画像は **image1〜image5 に直接添付**します
  （既存メッセージへの「返信」では画像を渡せないため、この形式にしています）
- `columns` は `layout:grid` の場合のみ有効（省略で自動）
- 例: `/merge layout:grid columns:2 image1:a.png image2:b.png image3:c.png image4:d.png`

### `/flip file:<画像> [direction:<horizontal/vertical>]`
- 画像を左右反転（水平ミラー）または上下反転（垂直フリップ）します
- `direction:horizontal`: 左右反転（自撮りの鏡像補正やイラストのデッサン・左右反転確認に最適）
- `direction:vertical`: 上下反転
- **右クリックから直接実行**: メッセージの画像を右クリック → **アプリ** → **画像を左右反転** でも一瞬で実行できます

### `/watermark file:<画像> text:<文字> [position] [opacity]`
- 画像に作者サインやID（`@username`）、`SAMPLE`、無断転載防止パターンなどの文字透かしを合成します
- `position`: `bottom_right`（右下サイン・角丸プレート付き） / `center`（中央に大きな斜め透かし） / `repeat`（全体に格子状に配置する無断転載防止パターン）
- `opacity`: `light`（控えめ） / `medium`（標準） / `strong`（くっきり）

### `/emoji file:<画像> [fit:<crop/pad>]`
- Discordのカスタム絵文字規格（128x128px・最大128KB）に自動最適化します
- `fit:crop` は中央正方形切り抜き、`fit:pad` は比率を維持したまま透明な余白を追加します
- 画像をタップして長押し（PCは右クリック）して保存し、サーバー設定からすぐ登録可能

### `/sticker file:<画像> [fit:<crop/pad>]`
- Discordのカスタムスタンプ規格（320x320px・最大512KB）に自動最適化します

### `/avatar_crop file:<画像>`
- 四角い写真をDiscordアイコン用に円形に切り抜き、四隅を完全透過にしたPNGを出力します

### `/strip_exif file:<画像>` ／ 右クリック「Exif情報を削除」
- GPS位置情報（撮影場所の緯度経度）、撮影日時、カメラ端末名、レンズ情報などのメタデータを完全抹消します

### `/palette file:<画像> [colors:3-8]`
- 画像から代表的なカラーを抽出し、見やすいカラーパレットカード画像とHEXカラーコード一覧を返します

### `/scan file:<ファイル>` ／ 右クリック「添付ファイルを検査」
- ファイルの拡張子が `.exe` `.bat` `.js` `.jar` `.ps1` など危険なものでないかチェックします
  （`.env`の`DANGEROUS_EXTENSIONS`でリストを変更可能）
- `.env`に`VIRUSTOTAL_API_KEY`を設定すると、SHA256ハッシュでVirusTotalに照会し、
  既知マルウェアかどうかも追加でチェックします（ファイル自体は送信せずハッシュのみ照会）
- 判定結果はチャンネルに埋め込みメッセージで表示するのみで、**メッセージの削除やDM送信などは一切行いません**
- 既に投稿されたメッセージをチェックしたい場合は、そのメッセージを右クリック（スマホは長押し）
  → **アプリ** → **添付ファイルを検査** から同じチェックを実行できます

### `/help [page] [command]`
- **ページ切り替え式一覧**（`command`未指定時）:
  - 4つのカテゴリページ（1. 変換・圧縮・結合・回転 / 2. 画像加工・絵文字・透かし・配色 / 3. リンク化・Exif削除・安全検査 / 4. 右クリック・便利機能・会員設定）に分けて表示します。
  - 「◀ 前へ」「次へ ▶」ボタンおよびカテゴリ選択ドロップダウンでページを切り替えられます（`page` 引数で最初から特定のページを開くことも可能）。
- **個別コマンド詳細**（`command`指定時）:
  - 指定したコマンドの詳しい使い方・パラメータ・実行例を表示し、「📖 コマンド一覧（ページ表示）に戻る」ボタンで一覧に戻れます。

### サーバー追加時の自動挨拶メッセージ（`on_guild_join`）
- Botが新しいサーバーに招待・追加されると、システムメッセージチャンネル（未設定・権限不足時は送信可能なテキストチャンネル）へ自動的に挨拶メッセージを送信します。
- メッセージ内容: `はじめまして！画像や動画を変幻自在に変換し化けさせるbot、**Convake(こんばけ)**です！` と主な機能・`/help` への案内を自動で届けます。

### `/admin`（開発者限定）
- 開発者（`OWNER_ID`）のみが実行可能な統合管理パネルです。実行者にのみ非公開（ephemeral）で表示されます。
- 埋め込みとインタラクティブボタン・モーダルにより、以下の管理操作をワンストップで実行できます:
  - 💎 **プレミアム付与**: 対象ユーザーID（またはメンション）と月数をモーダルで入力して即時登録（DM通知・ロール付与も自動実行）
  - ❌ **プレミアム解除**: 対象ユーザーのプレミアム登録およびロールを解除
  - 📋 **会員一覧**: 現在の有効会員・期限切れ会員の期限日時と残り日数を一覧表示
  - 🛒 **購入パネル設置**: 実行チャンネルに一般ユーザー向けのプレミアム購入案内パネル（「購入する」ボタン付き）を設置
  - 🧹 **メモリ解放(GC)**: 手動でガーベジコレクションを実行し、未使用メモリとオブジェクトを即時解放
  - 🔄 **情報更新**: 現在のメモリ使用量、稼働時間、Ping、会員数を最新情報に更新

> ※ 以前存在した外部チャンネルへのステータス定期自動送信Webhook（`STATUS_WEBHOOK_URL`）は削除されました。現在は`/admin`からリアルタイムにシステム診断を確認できます。

### 擬似保存機能（リアクション、唯一の非コマンド機能）
- 任意のメッセージに `📌`（`.env`の`SAVE_REACTION_EMOJI`で変更可）のリアクションを付けると、
  リアクションしたユーザーのDMにそのメッセージへのジャンプリンクが送信されます
- ユーザーがDMを許可していない場合は送信されません（コンソールにログのみ出力）

---

## メモリ管理について

長時間稼働してもメモリ使用量が際限なく増え続けないよう、以下の対策を組み込んでいます。

### 実測データ（起動時のベースメモリ使用量）
`/usr/bin/time -v` によるプロセス分離実測値（環境により多少変動します）:

| 構成 | 最大常駐メモリ |
|---|---|
| discord.pyのみ | 約45MB |
| + pyvips (libvips) | 約72MB |

以前は背景除去機能(rembg)がimportだけで約200MBを消費していましたが、
機能自体を廃止したためこのオーバーヘッドはなくなりました。

1. **libvips(pyvips)による画像処理の高速化・低メモリ化**
   `/convert`・`/compress`は利用可能であればlibvipsで処理します。libvipsはC言語で実装された
   ネイティブライブラリのため、大きな画像の変換・リサイズにおいてPillowよりメモリ効率が良い
   （実測: 単純な形式変換で使用メモリ約1/3）。未インストールでも自動的にPillowにフォールバックします。

2. **重い処理の同時実行数を制限**
画像・PDF・CSV・画像結合・動画変換などCPU/メモリを多く使う処理は、
同時実行数をセマフォで制限しています（`.env`の`MAX_CONCURRENT_HEAVY_TASKS`）。
ユーザー単位の処理中ロックと組み合わせ、同じユーザーの処理が無制限に積み上がらないようにしています。

3. **不要なキャッシュを無効化**
   このBotはメンバー一覧やメッセージ履歴のキャッシュに一切依存しない実装のため、
   discord.py側の以下のキャッシュを明示的に無効化しています。
   - メンバーキャッシュ・起動時の全メンバー取得（大規模サーバーで特に効果大）
   - メッセージキャッシュ（デフォルトで最大1000件保持されるものを無効化）

4. **処理後の明示的なガーベジコレクション**
   動画変換など、大きなバイト列を扱った処理の後は`gc.collect()`を呼び出し、
   不要になったメモリをなるべく早くOSに返せるようにしています。

5. **診断コマンド `/botstatus`**
   現在のメモリ使用量や一時ファイルサイズをいつでも確認でき、必要であれば手動でGCを実行できます。

**それでも長期運用でメモリが気になる場合の追加対策例:**
- `libvips`をOSにインストールする（未インストールの場合、画像処理がPillowのみで動作しメモリ効率が落ちる）
- systemdやDockerの再起動ポリシー、または`pm2`/`supervisor`等で定期再起動を設定する
- `MAX_CONCURRENT_HEAVY_TASKS`をさらに小さくする

---

## ディレクトリ構成
```
discord_bot/
├── main.py                # エントリーポイント（スラッシュコマンド同期含む）
├── config.py               # 環境変数の読み込み
├── requirements.txt
├── .env.example
├── fonts/                  # 日本語PDF変換用フォント配置場所（任意）
├── cogs/
│   ├── converter.py         # /convert, /compress
│   ├── merge.py              # /merge
│   ├── rotate.py             # /rotate
│   ├── image_tools.py        # /emoji, /sticker, /avatar_crop, /strip_exif, /palette
│   ├── link.py               # /link, /link_list, /link_delete
│   ├── reaction_save.py      # リアクション保存機能（唯一の自動機能）
│   ├── security.py           # /scan, 添付ファイルを検査（コンテキストメニュー）
│   ├── help.py                # /help
│   ├── premium.py             # プレミアム機能基盤, /premium_settings
│   └── admin.py               # /admin（開発者専用統合パネル: 付与/解除/一覧/設置/診断/GC）
└── utils/
    ├── file_helpers.py       # 添付ファイルの拡張子・種類判定
    ├── converters.py         # 変換ロジック本体（Pillow/libvipsハイブリッド）
    ├── image_tools.py        # 絵文字/スタンプ/丸型/Exif/パレット軽量処理ロジック
    ├── vips_backend.py        # libvipsバックエンド（利用不可時は自動フォールバック）
    ├── merger.py              # 画像結合ロジック
    ├── video.py               # 動画⇔GIF変換（ffmpeg）
    ├── security.py            # 危険拡張子/VirusTotalチェックロジック
    └── concurrency.py          # 重い処理の同時実行数を制限するセマフォ
```

---

## トラブルシューティング

### `429 Too Many Requests` でログインできない
起動直後に以下のようなエラーが出る場合、これは**Discord側がログイン試行そのものをブロックしている**状態で、
コードの不具合ではなく運用状況が原因であることがほとんどです。

```
discord.errors.HTTPException: 429 Too Many Requests (error code: 0): You are being blocked from accessing our API...
```

**主な原因と対処:**
1. **同じトークンで複数プロセスが同時に起動している**
   ホスティングパネルがクラッシュ時に古いプロセスを止めずに新しいプロセスを起動すると、
   同じトークンでの二重ログインとなり検知されやすくなります。プロセスが1つだけになっているか確認してください。
2. **短時間に再起動を繰り返している（クラッシュループ）**
   何らかのエラーで即クラッシュ→自動再起動→即クラッシュ…を繰り返すと、ログインAPIへの
   リクエストが連発しグローバルレート制限にかかります。本Botの`main.py`は失敗時に
   指数バックオフ（30秒→60秒→…最大15分）で待機してから再試行するようになっているため、
   この問題の再発は起きにくくなっていますが、**一度制限にかかった場合は数分〜1時間程度、
   起動を試みず待つ**のが確実です。
3. **共有ホスティングで同じIPの別Botが原因の場合もある**
   自分のBotのコードに問題がなくても、同一サーバーの他プロセスの影響を受けることがあります。

### 起動時に `Unclosed connector` という警告が出る
`main.py`では`async with bot:`パターンを使い、異常終了時も接続を確実にクローズするようにしています。
それでもこの警告が出続ける場合は、他に同じトークンを使っているプロセスが残っていないか確認してください。

---

## 注意事項
- `applications.commands` スコープでBotを招待しないと、スラッシュコマンドがサーバーに表示されません。
- 開発中は`.env`の`DEV_GUILD_ID`を設定してテストサーバーに即時同期することを推奨します（本番はグローバル同期）。
- `/scan`・添付ファイル検査はあくまで簡易チェックです。拡張子ブロックリストとVirusTotalのハッシュ照合（既知のマルウェアのみ検出）が中心のため、未知の脅威を完全に防げるものではありません。
- 動画変換や巨大な画像/PDF/CSVはCPU・メモリを使う重い処理です。設定した上限を超える入力は拒否されますが、大量利用が見込まれる場合はホスト側のリソースにもご注意ください。
- `/scan`は拡張子とVirusTotalの既知ハッシュ照合を中心とした簡易チェックであり、安全を保証するものではありません。
