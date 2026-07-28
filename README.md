# マニュアル検索

大量のPDFマニュアルを、**機種を選んで中身から検索**できるようにするシステムです。
スキャンしただけのPDFはOCRにかけ、章立てやエラーコードまで抜き出して索引にします。
ChatGPTのAPIキーを入れると、自然文で相談することもできます。

検索エンジンのサーバーは不要です。SQLite（FTS5）のDBファイル1つで動きます。

```
PDF ──▶ テキスト抽出 / OCR ──▶ 1冊ずつ分析 ──▶ SQLite(FTS5) ──▶ 検索画面
        (PyMuPDF/Tesseract)   章立て・コード      DBファイル1つ    └ AIに相談
                              ・機種の推定                          (任意)
```

## すぐ試す

**macOS / Linux**

```bash
./start.sh
```

**Windows**

`start.bat` をダブルクリック。

事前に [Python](https://www.python.org/downloads/) が必要です。インストール時に
**「Add python.exe to PATH」にチェック**を入れてください（入っていないと `start.bat` が
Pythonを見つけられません）。入っていなければ `start.bat` がその旨を表示して止まります。

初回だけ仮想環境の作成と依存関係のインストールが走ります（1〜2分）。
そのあと索引を作り、ブラウザで検索画面が開きます。2回目以降は増えたPDFだけ読み直すので、
起動は数秒です。

PDFが1つも無いときはサンプルを作るか聞かれるので、`y` で動作を確認できます。

## PDFの置きかた

`manuals/` の下に、**機種ごとのフォルダ**を作って入れてください。フォルダ名がそのまま
「使っている機械」の選択肢になります。

```
manuals/
├── ロボドリル/
│   ├── α-D21MiB5_操作説明書.pdf
│   └── α-D21MiB5_保守説明書.pdf
├── ワイヤーカット/
│   └── ...
└── library.csv        ← 一覧の台帳（任意）
```

## 使いかた

### 画面

| 画面 | できること |
|---|---|
| キーワード検索 | 機種を選んで型番・エラーコード・部品名で引く。ヒットしたページのPDFがその場で開く |
| AIに相談 | 「ロボドリルでSV0417が出た」のように書くと、根拠ページ付きで答える（APIキーが必要） |
| マニュアル一覧 | 登録済みマニュアルの一覧。機種・分類・メモを確認する |

選んだ機械はブラウザが覚えるので、毎回選び直す必要はありません。

### 検索の書きかた

| 入力 | 意味 |
|---|---|
| `エラー 点滅` | 両方を含むページ（AND） |
| `"冷却 ファン"` | 空白を含めてこの並びどおり |
| `ポンプ -旧型` | 「旧型」を含むページを除く |

全角・半角は自動でそろえるので、`Ｅ２０３` でも `E203` でも同じ結果になります。
日本語の空白や改行で割れた語（`マ ニ ュ ア ル`、行末で切れた `エラーコ`＋`ード`）も
取り込み時に直しているので、そのまま引けます。

> **3文字以上で引いてください。** 索引に使っている FTS5 の trigram トークナイザは
> 2文字以下の語を扱えません。1〜2文字だけの検索は全ページ走査になり、遅くなります。

## コマンド

`start.sh` / `start.bat` の代わりに、細かく操作したいとき。

```bash
python -m manualsearch <コマンド>
```

| コマンド | 用途 |
|---|---|
| `index <PDF置き場>` | フォルダごと取り込む。2回目以降は増減したPDFだけ処理する |
| `add <PDF> --machine ロボドリル` | **1冊だけ**取り込んで、分析結果を表示する |
| `analyze <PDF>` | 取り込まずに、章立て・コード・機種候補の下見だけする |
| `set <パス> --machine ...` | 取り込み済みマニュアルの機種・分類・名前を後から直す |
| `library [--init]` | 一覧の台帳を見る／PDFから下書きを作る |
| `search <語>` | 端末から検索する（`--machine` で機種を絞る） |
| `code E203` | エラーコードから該当ページを直接引く |
| `ask <質問>` | ChatGPTに相談する |
| `serve <PDF置き場>` | 検索画面を起動する |
| `stats` / `optimize` / `doctor` | 索引の状況確認・最適化・動作環境の確認 |

1冊ずつ確認しながら育てていく流れはこうなります。

```bash
python -m manualsearch analyze 新しいマニュアル.pdf          # まず中身を見る
python -m manualsearch add manuals/ロボドリル/新しいマニュアル.pdf \
       --machine ロボドリル --category 保守                  # 納得したら登録
python -m manualsearch set ロボドリル/新しいマニュアル.pdf \
       --title "α-D21 保守説明書 2024年版"                    # あとから直す
```

## 1冊ごとの分析でやっていること

取り込み時に、PDF1冊ずつ次を調べて索引に入れます。

- **章立て** — PDFのしおりを読む。しおりが無ければ `第3章 …` `3.2 …` `■ …` のような
  見出し行を本文から拾う。検索結果に「どの章の話か」が出ます。
- **ページと章の対応** — どのページがどの章に属するかを決めます。
- **エラーコード・アラーム番号** — 「エラーコード E203」のように前置きが付いた形を拾い、
  同じページに並ぶ一覧表のコードもまとめて拾います。`code` コマンドで直接引けます。
- **型番・機種の候補** — フォルダ名 → ファイル名の型番 → 表紙の型番、の順に推定します。
- **本文が取れているか** — 取れないページが多ければOCRを促します。

推定結果はすべて `library.csv` で上書きできます（下記）。

## マニュアル一覧（library.csv）

`manuals/library.csv` を置くと、ファイル名だけでは分からない情報を足せます。無くても
動きます。**Excelで開いて編集できます**（BOM付きUTF-8で読み書きします）。

```csv
path,machine,title,category,tags,note
ロボドリル/α-D21MiB5_操作説明書.pdf,ロボドリル,α-D21MiB5 操作説明書,操作,段取り アラーム,2024年改訂
```

| 列 | 意味 |
|---|---|
| `path` | PDF置き場からの相対パス（これだけ必須） |
| `machine` | 機種名。「使っている機械」の選択肢になる。空ならフォルダ名を使う |
| `title` | 画面に出す名前。空ならPDFのタイトル、それも無ければファイル名 |
| `category` / `tags` / `note` | 分類・タグ・メモ |

下書きは自動で作れます。

```bash
python -m manualsearch library manuals --init   # 見つかったPDFを全部並べたCSVを書き出す
# Excelで machine / title / category を埋める
python -m manualsearch index manuals            # 反映（PDFは読み直さないので一瞬）
```

## スキャンしたPDF（OCR）

テキストを持たないページだけを自動で見つけてOCRにかけます（`--ocr auto`、既定）。
テキストPDFとスキャンPDFが混ざった1冊でも、必要なページだけ処理します。

Tesseractの導入が必要です。

```bash
# Ubuntu / Debian
sudo apt install tesseract-ocr tesseract-ocr-jpn

# macOS
brew install tesseract tesseract-lang

# Windows
# https://github.com/UB-Mannheim/tesseract/wiki からインストール（言語で Japanese を選ぶ）
```

導入できているかは `python -m manualsearch doctor` で確認できます。

| 指定 | 動き |
|---|---|
| `--ocr auto` | 本文が取れないページだけOCR（既定） |
| `--ocr never` | OCRしない。テキストPDFだけを速く取り込みたいとき |
| `--ocr force` | 全ページOCR。文字が画像で埋め込まれている図面などに |

縦書きが多い場合は `--lang jpn+jpn_vert+eng` を付けてください。

## ChatGPT連携（任意）

APIキーを環境変数に入れて起動すると、「AIに相談」タブが出ます。

```bash
export OPENAI_API_KEY=sk-...
./start.sh
```

答えかたは次のようにしています。

1. 質問文から検索語を作る（APIが落ちていても簡易抽出で検索まではたどり着きます）
2. その語で全文検索し、根拠になりそうなページを最大8ページ集める
3. **集めたページの本文だけ**を渡して答えさせる

マニュアル全体は送りません。根拠が無ければ「記載が見つかりません」と答えるよう指示して
あり、答えの中の `[1]` をクリックすると該当ページのPDFが開きます。作業前には必ず根拠
ページを確認してください。

| 環境変数 | 既定 | 用途 |
|---|---|---|
| `OPENAI_API_KEY` | （未設定） | 設定しなければ相談機能だけ無効になる |
| `MANUAL_AI_MODEL` | `gpt-4o-mini` | 使うモデル |
| `OPENAI_BASE_URL` | （未設定） | Azure OpenAI や社内ゲートウェイを使う場合 |

## 設定

| 環境変数 | 既定 | 用途 |
|---|---|---|
| `MANUAL_ROOT` | `manuals` | PDFの置き場 |
| `MANUAL_DB` | `index.db` | 索引DBの場所 |
| `MANUAL_LIBRARY` | `library.csv` | 台帳のファイル名 |
| `MANUAL_OCR_LANG` | `jpn+eng` | Tesseractの言語 |
| `MANUAL_OCR_DPI` | `300` | OCR時の解像度 |
| `MANUAL_OCR_THRESHOLD` | `16` | この文字数未満のページをOCR対象とみなす |

## 社内で共有する

既定では自分のPCからしか繋がりません（`127.0.0.1`）。同じLANの人に使ってもらうときは
ホストを開きます。

```bash
HOST=0.0.0.0 ./start.sh
```

**認証は付いていません。** 社内LANの外に出す場合は、リバースプロキシ側でBasic認証や
IP制限をかけてください。PDFの配信はPDF置き場の中に限定してあり、索引に外部のパスが
入っていても配信しません。

索引を作り直す必要はなく、PDFを足したら `index` を流すだけです。夜間に流したいときは
cronやタスクスケジューラで次を実行してください。

```bash
.venv/bin/python -m manualsearch index manuals
```

## 規模の目安

| 項目 | 目安 |
|---|---|
| 索引DBの大きさ | 本文の1.5〜2倍程度（1万ページで数百MB） |
| 検索の速さ | 数万ページでも数十ms |
| 取り込みの速さ | テキストPDFは1冊数秒。OCRは1ページ1〜3秒（CPUの数だけ並列に処理します） |

数万冊を超えて全文検索の精度や表記ゆれに不満が出てきたら、この索引をそのまま
Meilisearch などに載せ替える余地があります。まずはこの構成で足ります。

## 開発

```bash
make setup   # 仮想環境と依存関係
make test    # テスト
make serve   # 索引を更新して起動
```

| ファイル | 役割 |
|---|---|
| `manualsearch/textnorm.py` | 全角半角・空白・改行の正規化（検索が当たるかはここで決まる） |
| `manualsearch/extract.py` | PDFからページ単位のテキスト抽出とOCR |
| `manualsearch/analyze.py` | 1冊ごとの分析（章立て・コード・機種の推定） |
| `manualsearch/library.py` | マニュアル一覧の台帳（library.csv） |
| `manualsearch/db.py` | SQLite + FTS5 のスキーマ |
| `manualsearch/indexer.py` | ディレクトリ走査と差分更新 |
| `manualsearch/search.py` | 検索とスコアリング |
| `manualsearch/assistant.py` | ChatGPT連携（RAG） |
| `manualsearch/web.py` | 検索画面（FastAPI） |
| `manualsearch/cli.py` | コマンドライン |
