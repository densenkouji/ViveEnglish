# ViveEnglish 設計ドキュメント

## 1. ねらいと学習設計

参考ブログの3本柱を、各レッスンの構成にそのまま対応させています。

| ブログの柱 | アプリ上の場所 | 具体 |
|---|---|---|
| ① 基礎固め | 「語彙・フレーズ」タブ | 自分が使う場面の単語・言い回しを、例文ごと音読（🔊）。覚える語を「単語帳」に保存。 |
| ② 大量インプット | 「読む・聞く」タブ | 身近なテーマの会話。単語タップ和訳・範囲選択和訳で、辞書を引く手間なく多読。🔊と連続再生で多聴。 |
| ③ 話すトレーニング | 「発話チェック」「AI対話」タブ | お手本の音読→録音/入力をAIが採点。Viviとのロールプレイで「自分の言いたいこと」を英語化。 |

ブログが強調する「3つを並行して回す」「音読のときに使う場面をイメージする」を、
1レッスン内で3タブを行き来できるUIと、フレーズの利用場面（📍）表示で支援します。

## 2. レッスンの粒度

- 1レッスン = 1テーマ・1場面。想定6〜10分（要件の5〜15分以内）。
- 語彙6・会話6行・フレーズ3・発話3・クイズ3 を基本フォーマットに統一。

## 3. データモデル（lessons.json）

```jsonc
{
  "id": "school-01",
  "theme": "School", "theme_ja": "学校",
  "level": "beginner|elementary|intermediate",
  "title_en": "...", "title_ja": "...",
  "est_minutes": 6,
  "summary_ja": "一覧カードの説明",
  "illustration": { "scene": "英語のシーン記述", "caption_ja": "...", "aspect": "3:2" },
  "warmup_ja": "ゴール提示",
  "grammar_points": [
    { "title_ja", "pattern", "explanation_ja", "examples": [] }
  ],
  "vocab":   [ { "en", "ja", "example_en", "example_ja" } ],
  "dialogue":[ { "speaker", "text", "ja" } ],
  "phrases": [ { "en", "ja", "when_ja" } ],
  "roleplay":{ "scenario", "scenario_ja", "opener", "opener_ja" },
  "speaking_lines": [ "発話チェックの対象文" ],
  "quiz":    [ { "q_ja", "type": "mc", "options": [], "answer": 0, "explain_ja" } ]
}
```

`grammar_points` は基礎文法レッスンなどで任意に表示されます。
`vocab` は、AIオフライン時の単語タップ和訳フォールバック辞書としても使われます。

## 4. AI連携（Foundry Local）

OpenAI互換エンドポイントに対し、用途別に1関数ずつ用意（`app/foundry.py`）。

- `translate(text, mode)` — 単語は「語義＋品詞」、文は「自然な和訳＋メモ」をJSONで返す。
- `tutor_reply(history, scenario, level)` — 役割固定の会話相手。返信・和訳・やさしい訂正・ヒントをJSON化。
- `check_speech(target, said, level)` — お手本と発話を比較し0–100で採点＋日本語フィードバック。
- 音声認識 — `nemotron-speech-streaming-en-0.6b` を `foundry-local-sdk` のライブ文字起こしで使用。

**フォールバック設計**：エンドポイント未検出・呼び出し失敗時も例外を投げず、
翻訳はレッスン語彙辞書、発話チェックは語一致ヒューリスティック、対話はオフライン案内に切り替え。
アプリ全体が止まらないことを最優先にしています。

### エンドポイント探索順
1. `FOUNDRY_BASE_URL`
2. `foundry-local-sdk`（`start_web_service()` のURL）
3. `FOUNDRY_FALLBACK_URL`（既定 `http://localhost:5273/v1`）

接続後は `/v1/models` から実際のモデルIDを取得し、`VIVE_CHAT_MODEL` を含むものを優先採用。

## 5. 永続化（SQLite）

`data/viveenglish.db` に4テーブル。

- `profile` — 表示名・レベル・挿絵スタイル・1日の目標。
- `lesson_progress` — 状態・最高点・実施回数・最終学習日時。
- `activity` — study/quiz/speak/chat の記録（連続日数とヒートマップ用）。
- `saved_words` — マイ単語帳。

リセットは `data/` を削除するだけ。

## 6. API一覧

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/health` | バージョン・AI状態 |
| POST | `/api/ai/reconnect` | Foundry Local再接続 |
| GET | `/api/themes` | テーマ一覧 |
| GET | `/api/lessons` | レッスンカード＋進捗 |
| GET | `/api/lessons/{id}` | レッスン本体 |
| GET | `/api/lessons/{id}/illustration?style=` | 合成済み挿絵プロンプト |
| GET | `/api/art-styles` | 挿絵スタイル定義 |
| POST | `/api/translate` | 単語/文の和訳 |
| POST | `/api/chat` | AI対話 |
| POST | `/api/speech/check` | 発話採点 |
| POST | `/api/speech/transcribe` | 音声→テキスト |
| GET/POST | `/api/progress` | 進捗取得/記録 |
| GET/POST | `/api/profile` | プロフィール取得/更新 |
| POST/DELETE | `/api/words` | 単語帳 |

## 7. フロントエンド

ビルド不要の素のSPA（`web/app.js`）。ハッシュルーティングで home/lessons/lesson/progress/profile。

- 単語タップ：本文を語トークンに分割（`tokenize`）し、クリックで `/translate` を呼びポップオーバー表示。
- 範囲和訳：選択テキストが本文内かつ複数語のとき「この範囲を和訳」ボタンを表示。
- 音読：`speechSynthesis`（オフラインでも動作）。
- 録音：`MediaRecorder` → Web Audio で 16kHz mono WAV に変換 → `/speech/transcribe`。
- 挿絵：選択スタイルを反映したプロンプトをコピー可能。

## 8. 今後の拡張余地

- 埋め込み（`qwen3-embedding`）による意味検索・自作教材のレコメンド。
- ストリーミング表示（Responses API）でAI対話を逐次描画。
- 学習者ごとのアカウント分離（現状は単一プロフィール）。
- 挿絵の自動生成（画像モデル連携）をアプリ内から実行。
