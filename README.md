# ViveEnglish 🌱

日本人の英語初心者〜中級者のための、AIインタラクティブ英語学習アプリ。
小学生から社会人まで、身近なテーマの短いレッスンで「読む・聞く・話す」を練習します。

> 設計思想：英語が話せるようになった人がみんなやっている3つのこと（① 基礎 ② インプット ③ アウトプット）を各レッスンに落とし込む。
> AI機能（翻訳・対話相手・発話チェック）は **Microsoft Foundry Local** を既定にしつつ、設定画面から **Ollama** や OpenAI互換エンドポイントにも切り替えられます。

---

## できること（要件との対応）

| 要件 | 実装 |
|---|---|
| 小学生〜社会人が対象 | レベル別（入門/初級/中級）。プロフィールで切替 |
| 単語/文脈単位でいつでも和訳 | 本文の英単語を**タップ**で語義表示、文を**ドラッグ選択**で範囲和訳（選択中のAIプロバイダーで翻訳、オフライン時はレッスン語彙にフォールバック） |
| 1コンテンツ5〜15分 | 各レッスンに想定時間を表示（6〜10分） |
| AIでインタラクティブ | AI「Vivi」とのロールプレイ対話、発話チェック（音声認識→正確さ採点） |
| 長文読解の補助 | 読解画面で入力文またはAI生成文を、主語・動詞・代名詞・前置詞/接続詞・5文型・段落役割・理由/結果/結論シグナルで色分け |
| 身近なテーマ | 学校・旅行・ビジネス・食事・ライフスタイル・日本文化（各2本＝計12本） |
| 進捗管理・モチベ維持 | 完了率・連続学習日数・60日ヒートマップ・テーマ別達成度・マイ単語帳・クイズ点 |
| 1レッスンに1枚以上の挿絵＋生成プロンプト | 全レッスンに挿絵シーン記述＋生成プロンプト。テイストは**全レッスン一括設定**（水彩絵本/フラット/アニメ/色鉛筆） |

---

## 動かし方

### 1. アプリ本体（これだけで動きます）

Windows:
```bat
run.bat
```
macOS / Linux:
```bash
./run.sh
```
または手動で:
```bash
pip install -r requirements.txt
python run.py        # http://localhost:8000 が開きます
```

Foundry Local が無くても、語彙・本文・音読（ブラウザ音声合成）・クイズ・進捗・挿絵プロンプトはすべて使えます。
AI機能（翻訳/対話/発話チェック）は接続時に自動で有効になります。

### 2. AI（Foundry Local / Ollama）を有効にする

設定画面の **AI接続** で、会話・翻訳・採点に使うプロバイダーを選べます。

- **Foundry Local**: 既定。アプリが空きポートで起動・検出し、モデルのダウンロード・削除管理もできます。
- **Ollama**: `ollama serve` などで起動済みの Ollama に接続します。既定URLは `http://localhost:11434/v1` です。
- **OpenAI互換URL**: `/v1/chat/completions` と `/v1/models` を持つ任意の互換APIに接続できます。

Ollama の例:

```bash
ollama pull qwen2.5:3b
ollama serve
```

その後、設定画面でプロバイダーを `Ollama` にし、会話モデル名に `qwen2.5:3b` などを指定します。

### Foundry Local を使う場合

Foundry Local をインストールして起動すると、OpenAI互換エンドポイントが `localhost` に立ち上がります。

```bash
# 例: Python SDK
pip install foundry-local-sdk          # macOS/Linux
pip install foundry-local-sdk-winml    # Windows
```

`run.bat` / `run.sh` で起動する場合は、SDK が未導入なら起動時に自動インストールを試みます。

**ViveEnglish が起動時に空きポートを確保し、そのポートを指定して Foundry Local を起動します。**
Foundry Local は起動のたびに動的ポートを使うため固定ポートだと繋がりませんが、本アプリは
`foundry-local-sdk` の `Configuration(web={"urls": ...})` を使って**こちらが選んだポートで起動**するので、
ポートを意識する必要はありません。

**やること（これだけ）**

```bash
# 手動で入れる場合
pip install foundry-local-sdk-winml   # Windows / mac・Linux は foundry-local-sdk
```

通常は `run.bat`（または `run.sh`）で ViveEnglish を起動するだけ。アプリが空きポートを選び、
Foundry Local をそのポートで立ち上げて自動接続します。右上の「AI」インジケータが緑になれば接続完了。

> 初回起動時は、既定のチャット用モデル
> `qwen3.5-2b-text-generic-cpu` をバックグラウンドでダウンロードして読み込みます。
> 学習画面は先に使えます。モデル準備中は設定画面または初回セットアップ表示で進行状況を確認できます。

**動作の設定（環境変数・すべて任意）**

| 変数 | 既定 | 説明 |
|---|---|---|
| `VIVE_AI_PROVIDER` | `foundry` | 会話・翻訳・採点の接続先。`foundry` / `ollama` / `openai` |
| `VIVE_AI_BASE_URL` | （未設定） | OllamaやOpenAI互換URLの接続先。例: `http://localhost:11434/v1` |
| `VIVE_AI_API_KEY` | `notneeded` | OpenAI互換URLでAPIキーが必要な場合に指定 |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama選択時の既定URL |
| `VIVE_MANAGE_FOUNDRY` | `1` | アプリが空きポートで Foundry Local を起動する。`0`で無効化 |
| `VIVE_FOUNDRY_PORT` | 自動 | 使うポートを固定したいとき指定 |
| `VIVE_FOUNDRY_HOST` | `127.0.0.1` | バインド先ホスト |
| `VIVE_AUTOLOAD_MODEL` | `1` | チャットモデルを起動時に自動ダウンロード/ロード |
| `VIVE_CHAT_MODEL` | `qwen3.5-2b-text-generic-cpu` | 翻訳・対話・採点に使うモデル（`foundry model list`で確認） |
| `VIVE_TRANSLATE_MODEL` | （未設定） | 和訳・添削だけ別モデルにしたいとき指定。未設定なら `VIVE_CHAT_MODEL` を使う |
| `VIVE_TRANSCRIBE_MODEL` | `whisper-base` | 発話チェックの音声認識(STT)モデル。Whisper系を推奨（`whisper-tiny`/`small`等も可） |
| `FOUNDRY_BASE_URL` | （未設定） | 外部で起動済みの Foundry Local に**手動接続**したいとき（指定すると自動起動より優先） |

外部管理のサービスに繋ぐ場合のみ、`foundry service status` でURLを確認し
`FOUNDRY_BASE_URL=http://127.0.0.1:ポート/v1` を設定してください。

画面右上の「AI」インジケータが緑なら接続中。クリックで再接続（＝必要なら再起動）できます。

**チャット用モデルについて（重要）**

チャット・翻訳・発話採点には **text（chat-completion）対応モデル** が必要です。
ViveEnglish は利用可能なモデルから自動で chat 対応モデルを選び、**Vision（`*-vl*` など）・
embedding・音声認識（whisper/speech）系のモデルは選ばないように**除外します。
（補足：`qwen3.5-2b-text-generic-cpu` や `qwen2.5-0.5b-instruct` 系は Vision ではなく text のチャットモデルです。`-vitis-npu` などの
末尾は実行ハードウェア向けのバリアント名です。）

明示的に使うモデルを固定したいときは、手元にある chat モデルを確認して指定します。

```bash
foundry model list --filter task=chat-completion   # チャット対応モデルを一覧
# 例: 既定のCPU向け軽量テキストモデルを明示する
export VIVE_CHAT_MODEL=qwen3.5-2b-text-generic-cpu
# PowerShell: $env:VIVE_CHAT_MODEL="qwen3.5-2b-text-generic-cpu"
```

小さいモデルで和訳が英文のまま返る場合は、日本語に強い大きめの chat モデルを取得し、
`VIVE_TRANSLATE_MODEL` に指定してください。会話は軽量モデル、和訳・添削だけ大きめのモデルに分けられます。

万一 text 非対応のモデルしか見つからない場合、「AI」状態に警告メッセージが表示されます。

---

## 使い方（レッスンの流れ）

各レッスンはタブで3つの柱に沿って進みます。

1. **① 語彙・フレーズ（基礎固め）** — 使う場面の単語と言い回しを例文ごと音読。
2. **② 読む・聞く（大量インプット）** — 会話を読む。単語タップで和訳、🔊で発音、続けて再生でリスニング。
3. **③ 発話チェック（話すトレーニング）** — お手本を聞き、録音 or 入力でAIが発音・正確さを採点。
4. **AI対話** — 「Vivi」とロールプレイ。やさしい訂正と和訳つき。
5. **クイズ** — 理解度チェック。全問解くとレッスン完了として記録。
6. **挿絵** — このレッスンの画像生成プロンプト（設定したテイストが反映）。

トップナビの **読解** では、任意の英文を貼り付けるかAIで長文を生成して、文構造・5文型・段落の役割・重要な接続語や指示語を色分けで確認できます。

---

## 技術構成

```
ViveEnglish/
├─ app/                  FastAPI バックエンド
│  ├─ main.py            APIルート + 静的配信
│  ├─ foundry.py         AIプロバイダー接続（Foundry/Ollama/OpenAI互換、オフライン耐性）
│  ├─ database.py        SQLite（進捗・プロフィール・単語帳）
│  ├─ content_store.py   レッスン/挿絵プロンプト読み込み
│  ├─ config.py          設定（環境変数）
│  └─ content/
│     ├─ lessons.json    22レッスン
│     └─ art_styles.json 挿絵スタイル・プリセット
├─ web/                  フロントエンド（素のHTML/CSS/JS）
│  ├─ index.html
│  ├─ styles.css
│  └─ app.js
├─ data/                 SQLite DB（自動生成・リセットはこのフォルダ削除）
├─ requirements.txt
├─ run.py / run.bat / run.sh
└─ docs/
   ├─ DESIGN.md          設計・要件対応の詳細
   └─ ILLUSTRATION_GUIDE.md  挿絵プロンプト運用ガイド
```

- バックエンド：FastAPI + uvicorn、永続化はSQLite（stdlibのみ）。
- AI呼び出し：OpenAI互換クライアントで Foundry Local / Ollama / 任意の互換APIを叩く。未接続でも全体が壊れないフォールバック設計。
- フロント：ビルド不要の素のSPA。音読はブラウザの音声合成、録音は MediaRecorder→16kHz WAV変換でサーバ送信。

---

## コンテンツの増やし方

`app/content/lessons.json` に1レッスン分のオブジェクトを追加するだけで反映されます
（`grammar_points` / `vocab` / `dialogue` / `phrases` / `roleplay` / `speaking_lines` / `quiz` / `illustration`）。
スキーマは `docs/DESIGN.md` を参照してください。

---

## ライセンス / 注意

学習用プロトタイプです。Foundry Local のモデル利用は各モデルのライセンスに従ってください。
