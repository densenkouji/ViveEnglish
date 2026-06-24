# ViveEnglish 🌱

日本人の英語初心者〜中級者のための、AIインタラクティブ英語学習アプリです。
身近なテーマの短いレッスンで「読む・聞く・話す」を練習します。

**本アプリは英語を学習する際にこんな機能があったらいいな、というものをAIに実装してもらったものです。** <br>
**内容に不正確さを含む場合があるため参考程度にご覧ください。**

> 設計思想：英語を話せるようになるための3本柱（① 基礎 ② インプット ③ アウトプット）を各レッスンに落とし込む。
> AI機能（翻訳・対話相手・発話チェック）は **Microsoft Foundry Local** を既定にしつつ、設定画面から **Ollama**・**OpenAI (ChatGPT)**・**Azure OpenAI**・任意のOpenAI互換エンドポイントにも切り替えられます。

---

## できること

| 要件 | 実装 |
|---|---|
| 小学生〜社会人が対象 | レベル別（入門/初級/中級）。プロフィールで切替 |
| 単語/文脈単位でいつでも和訳 | 本文の英単語を**タップ**で語義表示、文を**ドラッグ選択**で範囲和訳（選択中のAIプロバイダーで翻訳、オフライン時はレッスン語彙にフォールバック） |
| 1コンテンツ5〜15分 | 各レッスンに想定時間を表示（6〜10分） |
| AIでインタラクティブ | AIとのロールプレイ対話、発話チェック（音声認識→正確さ採点） |
| 身近なテーマ | 学校・旅行・ビジネス・食事・ライフスタイル・日本文化 |
| 進捗管理・モチベ維持 | 完了率・連続学習日数・60日ヒートマップ・テーマ別達成度・マイ単語帳・クイズ |

### テスト中の機能
| 要件 | 実装 |
|---|---|
| 長文読解サポート | 読解画面で入力文またはAI生成文を、主語・動詞・代名詞・前置詞/接続詞・5文型・段落役割・理由/結果/結論シグナルで色分け |

---

## AIについての注意事項
- AIは使用するモデルにより生成されるコンテンツの精度が大きく変わります
- デフォルトでインストールされるモデルは動作確認用の比較的小さいモデルを指定しているため、AIの応答に不足や不備がある場合はよりサイズの大きいモデルを試してください（設定画面から変更可能です）
- AIの生成する内容には誤り（ハルシネーション）が含まれる場合があるため、予めご了承ください

---
## 動かし方

### 0. 事前準備

- 動作確認はEdgeブラウザにて行っています
- 実行にはPythonが必要なため、未インストールの場合は次のコマンドをPowerShellで実行してください

Windows:
``` PowerShell
winget install --id Python.Python.3.14
```

### 1. アプリの実行

Windows: PowerShellにて実行
```bat
.\run.bat
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

Foundry Local が無くても、語彙・本文・音読（ブラウザ音声合成）・クイズ・進捗など利用可能です。
AI機能（翻訳/対話/発話チェック）は接続時に自動で有効になります。

### 2. AI（Foundry Local / Ollama / OpenAI / Azure OpenAI）を有効にする

設定画面の **AI接続** で、会話・翻訳・採点に使うプロバイダーを選べます。

- **Foundry Local**: 既定。アプリが空きポートで起動・検出し、モデルのダウンロード・削除管理もできます。
- **Ollama**: `ollama serve` などで起動済みの Ollama に接続します。既定URLは `http://localhost:11434/v1` です。
- **OpenAI (ChatGPT)**: OpenAI公式API（`api.openai.com`）に接続します。会話モデルに `gpt-4o-mini` などを指定します。
- **Azure OpenAI**: Azure 上にデプロイした OpenAI モデルに接続します。エンドポイント・APIバージョン・デプロイ名を指定します。
- **OpenAI互換URL**: `/v1/chat/completions` と `/v1/models` を持つ任意の互換APIに接続できます。

#### APIキーの扱い（OpenAI / Azure OpenAI）

OpenAI (ChatGPT) と Azure OpenAI では、**APIキーそのものはアプリに保存しません**。
設定画面では「APIキーを保持する**環境変数名**」を入力し、実際のキーは起動時にその環境変数から読み取ります。

```bash
# 例: OpenAI (ChatGPT)
export OPENAI_API_KEY="sk-..."        # macOS / Linux
setx OPENAI_API_KEY "sk-..."          # Windows（新しいシェルから有効）

# 例: Azure OpenAI
export AZURE_OPENAI_API_KEY="..."
```

環境変数を設定したうえでアプリを起動し、設定画面でプロバイダーと環境変数名（既定: `OPENAI_API_KEY` / `AZURE_OPENAI_API_KEY`）を保存してください。
Azure の場合は併せて、エンドポイント（例: `https://<resource>.openai.azure.com`）・APIバージョン（例: `2024-10-21`）・**デプロイ名**（会話モデル欄に入力）を指定します。

#### Ollama の例

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
| `VIVE_AI_PROVIDER` | `foundry` | 会話・翻訳・採点の接続先。`foundry` / `ollama` / `openai` / `chatgpt` / `azure` |
| `VIVE_AI_BASE_URL` | （未設定） | OllamaやOpenAI互換URLの接続先。例: `http://localhost:11434/v1` |
| `VIVE_AI_API_KEY` | `notneeded` | OpenAI互換URLでAPIキーが必要な場合に指定 |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama選択時の既定URL |
| `VIVE_OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI (ChatGPT) の接続先 |
| `VIVE_OPENAI_API_KEY_ENV` | `OPENAI_API_KEY` | OpenAIのAPIキーを保持する環境変数の**名前**（既定の環境変数名） |
| `VIVE_OPENAI_CHAT_MODEL` | `gpt-4o-mini` | OpenAI (ChatGPT) の既定会話モデル |
| `VIVE_AZURE_OPENAI_ENDPOINT` | （未設定） | Azure OpenAI のエンドポイント。例: `https://<resource>.openai.azure.com` |
| `VIVE_AZURE_OPENAI_API_VERSION` | `2024-10-21` | Azure OpenAI のAPIバージョン |
| `VIVE_AZURE_OPENAI_API_KEY_ENV` | `AZURE_OPENAI_API_KEY` | AzureのAPIキーを保持する環境変数の**名前**（既定の環境変数名） |
| `VIVE_AZURE_OPENAI_DEPLOYMENT` | （未設定） | Azure OpenAI の既定デプロイ名（会話モデル） |
| `OPENAI_API_KEY` | （未設定） | OpenAIのAPIキー本体。`VIVE_OPENAI_API_KEY_ENV` で名前を変更可 |
| `AZURE_OPENAI_API_KEY` | （未設定） | AzureのAPIキー本体。`VIVE_AZURE_OPENAI_API_KEY_ENV` で名前を変更可 |
| `VIVE_MANAGE_FOUNDRY` | `1` | アプリが空きポートで Foundry Local を起動する。`0`で無効化 |
| `VIVE_FOUNDRY_PORT` | 自動 | 使うポートを固定したいとき指定 |
| `VIVE_FOUNDRY_HOST` | `127.0.0.1` | バインド先ホスト |
| `VIVE_AUTOLOAD_MODEL` | `1` | チャットモデルを起動時に自動ダウンロード/ロード |
| `VIVE_CHAT_MODEL` | `qwen3.5-2b-text-generic-cpu` | 翻訳・対話・採点に使うモデル（`foundry model list`で確認） |
| `VIVE_TRANSLATE_MODEL` | （未設定） | 和訳・添削だけ別モデルにしたいとき指定。未設定なら `VIVE_CHAT_MODEL` を使う |
| `VIVE_TRANSCRIBE_MODEL` | `whisper-base` | 発話チェックの音声認識(STT)モデル。Whisper系を推奨（`whisper-tiny`/`small`等も可） |
| `VIVE_READING_MIN_PARAMS_B` | `7` | 長文読解のAI解析を許可するローカルモデルの最小規模（十億パラメータ）。これ未満の Foundry/Ollama モデルは簡易解析になる |
| `VIVE_READING_FORCE_AI` | `0` | `1`で長文読解のAI解析を常に有効化（モデル名にサイズが出ない高性能ローカルモデル向け） |
| `VIVE_READING_DISABLE_AI` | `0` | `1`で長文読解のAI解析を常に無効化（必ず簡易解析を使う） |
| `VIVE_READING_DEBUG` | `0` | `1`で、簡易解析に落ちた文とその理由を `data/reading_debug.log` に記録（「一部の文はAI解析が崩れた」原因の切り分け用） |
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

**長文読解のAI解析について（モデル要件）**

長文読解の文構造解析（主語・動詞・5文型・段落役割など）は、文ごとに厳密なJSONを返す高度なタスクです。
2B程度の小型ローカルモデルでは構造の誤判定や英語ラベルの混入が起きやすいため、**高機能なLLM向けに最適化**しています。

- ChatGPT / Azure OpenAI / OpenAI互換（hosted）プロバイダーでは常にAI解析が有効です。
- Foundry Local / Ollama では、モデル名から推定した規模が `VIVE_READING_MIN_PARAMS_B`（既定7B）以上のときだけAI解析が有効になります。
- 条件を満たさない場合は、ルールベースの**簡易解析**を表示し、画面に高機能LLMの利用を促す案内を出します（他機能には影響しません）。
- モデル名に規模表記（`-7b` など）が無い高性能ローカルモデルを使う場合は、`VIVE_READING_FORCE_AI=1` でAI解析を強制有効化できます。
- 推論（reasoning）系モデル（o-series / gpt-5 系など）では、内部推論にトークンを使い切って本文が空で返ることがあります。ViveEnglish は空応答を検知するとトークン上限を自動的に引き上げて再試行します。それでも一部の文が簡易解析になる場合は、`VIVE_READING_DEBUG=1` で `data/reading_debug.log` を確認してください（`finish_reason=length` なら上限到達が原因）。

---

## 使い方（レッスンの流れ）

各レッスンはタブで3つの柱に沿って進みます。

1. **① 語彙・フレーズ（基礎固め）** — 使う場面の単語と言い回しを例文ごと音読
2. **② 読む・聞く（大量インプット）** — 会話を読む。単語タップで和訳、🔊で発音、続けて再生でリスニング
3. **③ 発話チェック（話すトレーニング）** — お手本を聞き、録音 or 入力でAIが発音・正確さを採点
4. **AI対話** — AIとロールプレイチャット
5. **クイズ** — 理解度チェックを全問解くとレッスン完了として記録

トップナビの **読解** では、任意の英文を貼り付けるかAIで長文を生成して、文構造・5文型・段落の役割・重要な接続語や指示語を色分けで確認できます。

---

## 技術構成

```
ViveEnglish/
├─ app/                  FastAPI バックエンド
│  ├─ main.py            APIルート + 静的配信
│  ├─ foundry.py         AIプロバイダー接続（Foundry/Ollama/OpenAI/Azure/互換、オフライン耐性）
│  ├─ database.py        SQLite（進捗・プロフィール・単語帳）
│  ├─ content_store.py   レッスン/挿絵プロンプト読み込み
│  ├─ config.py          設定（環境変数）
│  └─ content/
│     ├─ lessons.json    レッスン
│     └─ art_styles.json 挿絵スタイル・プリセット
├─ web/                  フロントエンド（素のHTML/CSS/JS）
│  ├─ index.html
│  ├─ styles.css
│  └─ app.js
├─ data/                 SQLite DB（自動生成・リセットはこのフォルダ削除）
├─ requirements.txt
└─ run.py / run.bat / run.sh
```

- バックエンド：FastAPI + uvicorn、永続化はSQLite（stdlibのみ）。
- AI呼び出し：OpenAI互換クライアントで Foundry Local / Ollama / OpenAI (ChatGPT) / Azure OpenAI / 任意の互換APIを叩く。未接続でも全体が壊れないフォールバック設計。OpenAI/AzureのAPIキーは環境変数名で指定し、キー本体はアプリに保存しない。
- フロント：ビルド不要の素のSPA。音読はブラウザの音声合成、録音は MediaRecorder→16kHz WAV変換でサーバ送信。

---

## コンテンツの増やし方

`app/content/lessons.json` に1レッスン分のオブジェクトを追加するだけで反映されます
（`grammar_points` / `vocab` / `dialogue` / `phrases` / `roleplay` / `speaking_lines` / `quiz` / `illustration`）。
スキーマは `docs/DESIGN.md` を参照してください。

---

## ライセンス / 注意

学習用プロトタイプです。Foundry Local のモデル利用は各モデルのライセンスに従ってください。
