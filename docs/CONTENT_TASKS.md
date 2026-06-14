# コンテンツ追加ロードマップ（文法・学習スキル）

ユーザー提案の20項目を、1項目=1レッスンとして分割追加するための管理表です。
**毎回このファイルの状態（☐/☑）を更新しながら、数本ずつ追加**していきます。

## 進め方（1レッスンの追加手順）

1. `app/content/lessons.json` の配列末尾（最後のレッスンの `}` の後）に新レッスンを追記。
   - スキーマは下記。文法レッスンは `grammar_points` を必ず入れる。
2. 挿絵プレースホルダーSVGを作成し `web/illustrations/<id>.svg` に配置。
   - 生成は `tools/gen_lesson_svg.py`（テーマ色＋モチーフ）または既存スクリプトを流用。
3. 追記ブロックをJSON単体で検証（`python -m json.tool` 等）。
4. このファイルの該当行を ☑ に更新。
5. アプリ再起動で「レッスン」一覧に反映（テーマで絞り込み可）。

> 注意：`lessons.json` は大きいため、編集はエディタ（実ファイル）で行う。
> 追記時の「アンカー」は常に「現在の最後のレッスンの最終 quiz 行＋ `]} }`」を使う。

## レッスンのスキーマ（再掲）

```jsonc
{
  "id": "gram-tenses",
  "theme": "Grammar Basics", "theme_ja": "基礎文法",   // 文法は左、スキルは "Study Skills"/"学習スキル"
  "level": "beginner|elementary|intermediate",
  "title_en": "...", "title_ja": "...",
  "est_minutes": 10,
  "summary_ja": "一覧カードの説明",
  "illustration": { "scene": "英語のシーン記述", "caption_ja": "...", "aspect": "3:2" },
  "warmup_ja": "このレッスンのゴール",
  "grammar_points": [ { "title_ja": "", "pattern": "", "explanation_ja": "", "examples": ["", ""] } ],
  "vocab":   [ { "en": "", "ja": "", "example_en": "", "example_ja": "" } ],   // 6語
  "dialogue":[ { "speaker": "", "text": "", "ja": "" } ],                       // 6行
  "phrases": [ { "en": "", "ja": "", "when_ja": "" } ],                         // 3
  "roleplay":{ "scenario": "", "scenario_ja": "", "opener": "", "opener_ja": "" },
  "speaking_lines": [ "", "", "" ],                                            // 3
  "quiz":    [ { "q_ja": "", "type": "mc", "options": ["","",""], "answer": 0, "explain_ja": "" } ]  // 3〜4
}
```

## 文法シリーズ（theme: Grammar Basics / 基礎文法）

| # | 項目 | lesson id | level | 目安 | 状態 | 備考 |
|---|------|-----------|-------|------|------|------|
| 1 | 基本文型（第1〜第5文型） | `gram-sentence-patterns` | intermediate | 10分 | ☑ | 作成済み（テンプレート） |
| 2 | 品詞 | `gram-parts-of-speech` | beginner | 9分 | ☑ | 名詞/動詞/形容詞/副詞 |
| 3 | 時制 | `gram-tenses` | intermediate | 11分 | ☑ | 現在/過去/未来/進行/完了の俯瞰 |
| 4 | 助動詞 | `gram-modals` | elementary | 9分 | ☑ | can/will/should/must/may/would like |
| 5 | 名詞・代名詞 | `gram-nouns-pronouns` | beginner | 9分 | ☑ | 可算/不可算・人称/所有/目的格 |
| 6 | 冠詞 | `gram-articles` | beginner | 8分 | ☑ | a/an/the/無冠詞（grammar-03 を補完） |
| 7 | 前置詞 | `gram-prepositions` | elementary | 9分 | ☑ | at/on/in/to/for… 時と場所 |
| 8 | 接続詞 | `gram-conjunctions` | elementary | 9分 | ☑ | and/but/because/when/if |
| 9 | 比較 | `gram-comparison` | elementary | 10分 | ☑ | 原級/比較級/最上級 |
| 10 | 不定詞 | `gram-infinitives` | intermediate | 10分 | ☑ | 名詞/形容詞/副詞的用法 |
| 11 | 動名詞 | `gram-gerunds` | intermediate | 9分 | ☑ | 不定詞との使い分け |
| 12 | 分詞 | `gram-participles` | intermediate | 10分 | ☑ | 現在/過去分詞・感情の -ing/-ed |
| 13 | 関係代名詞 | `gram-relative-pronouns` | intermediate | 11分 | ☑ | who/which/that |
| 14 | 間接疑問文 | `gram-indirect-questions` | intermediate | 9分 | ☑ | I know where he is. |
| 15 | 仮定法 | `gram-subjunctive` | intermediate | 11分 | ☑ | If I were… / I wish… |
| 16 | 命令文・感嘆文 | `gram-imperatives-exclamations` | beginner | 8分 | ☑ | Be quiet. / How nice! |

## 学習スキルシリーズ（theme: Study Skills / 学習スキル）

| # | 項目 | lesson id | level | 目安 | 状態 | 備考 |
|---|------|-----------|-------|------|------|------|
| 17 | 読解の基礎 | `skill-reading` | elementary | 10分 | ☑ | 主語動詞の把握・推測読み |
| 18 | 英作文の基礎 | `skill-writing` | elementary | 10分 | ☑ | 短文を正確に・型で書く |
| 19 | リスニングの基礎 | `skill-listening` | beginner | 9分 | ☑ | 音の連結・キーワード聴き |
| 20 | スピーキングの基礎 | `skill-speaking` | beginner | 9分 | ☑ | 音読→言い換え→発話 |

## 推奨バッチ（分割例）

- バッチA：1〜4（文型・品詞・時制・助動詞）
- バッチB：5〜9（名詞代名詞・冠詞・前置詞・接続詞・比較）
- バッチC：10〜13（不定詞・動名詞・分詞・関係代名詞）
- バッチD：14〜16（間接疑問・仮定法・命令/感嘆）
- バッチE：17〜20（読解・英作文・リスニング・スピーキング）

各バッチ完了時に本ファイルを更新する。

---

# 不足コンテンツの検討と追加候補（拡張ロードマップ）

対象は「小学生〜社会人の初級〜中級」。現状を棚卸しした結果、以下が不足しています。
既存テーマ：School(2)・Travel(2)・Business(6)・Food(2)・Lifestyle(2)・Japanese Culture(2)、
基礎文法(grammar-01〜04 ＋ gram-* 系)、学年別(中1〜高3)、学習スキル(4)。

## ✅ 抜けの解消（旧・最優先 = バッチF：完了）

| 項目 | lesson id | 状態 | 備考 |
|---|---|---|---|
| 品詞 | `gram-parts-of-speech` | ☑ | 提案20の#2。バッチFで作成 |
| 時制（俯瞰） | `gram-tenses` | ☑ | 提案20の#3。バッチFで作成 |
| 助動詞 | `gram-modals` | ☑ | 提案20の#4。バッチFで作成 |

> バッチF完了により、**提案20項目すべて（#1〜#20）が実装済み**になりました。
> 以降は下記 G 以降の拡張候補を順次進めます。

## バッチG：文法の発展（theme: Grammar Basics / 基礎文法）

提案20には無いが実用上重要な文法。grade-* で断片的に触れているものを独立レッスン化。

| # | 項目 | lesson id | level | 備考 |
|---|---|---|---|---|
| 21 | 現在完了形 | `gram-present-perfect` | intermediate | have+過去分詞（経験/継続/完了）, ever/never/for/since |
| 22 | 受動態 | `gram-passive` | intermediate | be+過去分詞, by〜, 能動↔受動 |
| 23 | 時制の各論 | `gram-tense-details` | elementary | 現在進行/過去進行/will と be going to の違い |
| 24 | 助動詞の発展 | `gram-modals-advanced` | intermediate | have to / had better / used to / would like |
| 25 | 関係副詞・whose | `gram-relative-adverbs` | intermediate | where/when/why, 所有の whose, 目的格の省略 |
| 26 | 数量表現 | `gram-quantifiers` | beginner | some/any/many/much/a few/a little |
| 27 | there is / are 構文 | `gram-there-is` | beginner | 存在を表す表現と数の一致 |
| 28 | 付加疑問・否定疑問 | `gram-tag-questions` | elementary | ~, isn't it? / Don't you ~? |

## バッチH：日常会話シーンの拡充（不足テーマ）

身近な場面が School/Travel/Food/Lifestyle 各2本と手薄。利用頻度の高い場面を追加。

| # | 項目 | lesson id | 推奨テーマ(theme/theme_ja) | level | 備考 |
|---|---|---|---|---|---|
| 29 | 買い物（店で） | `shopping-01` | Shopping / 買い物 | elementary | 試着・値段・支払い |
| 30 | 体調・病院 | `health-01` | Health / 健康・医療 | elementary | 症状を伝える・薬局 |
| 31 | ホテルでの会話 | `travel-03` | Travel / 旅行 | elementary | チェックイン・要望・トラブル |
| 32 | 電車・バス・道案内 | `travel-04` | Travel / 旅行 | beginner | 切符・乗り換え・運賃 |
| 33 | 天気・季節の話 | `lifestyle-03` | Lifestyle / ライフスタイル | beginner | 天気予報・服装 |
| 34 | 電話・メッセージ(日常) | `lifestyle-04` | Lifestyle / ライフスタイル | elementary | 約束・SNS・短い連絡 |
| 35 | 緊急・トラブル | `travel-05` | Travel / 旅行 | elementary | 落とし物・迷子・助けを求める |
| 36 | 役所・銀行・郵便 | `errands-01` | Daily Errands / 手続き | intermediate | 申請・口座・発送 |
| 37 | 家族・自己紹介(深掘り) | `school-03` | School / 学校 | beginner | 家族構成・好み・性格 |
| 38 | 日本文化(祭り・温泉等) | `culture-03` | Japanese Culture / 日本文化 | intermediate | 季節行事・マナー説明 |

## バッチI：発音・フォニックス（新テーマ：Pronunciation / 発音）

リスニング/スピーキング基礎はあるが、音そのものの体系が無い。小学生のフォニックスにも有効。

| # | 項目 | lesson id | level | 備考 |
|---|---|---|---|---|
| 39 | 母音の基本 | `pron-vowels` | beginner | a/e/i/o/u と長短・あいまい母音 |
| 40 | 子音の基本 | `pron-consonants` | beginner | l/r, b/v, th, s/sh など日本人が苦手な音 |
| 41 | 語と文の強勢 | `pron-stress` | elementary | アクセント位置・内容語を強く |
| 42 | イントネーション | `pron-intonation` | elementary | 上げ下げで意味・気持ちを表す |
| 43 | 音の連結（リエゾン） | `pron-linking` | elementary | wanna/gonna・連結・脱落 |

## バッチJ：テーマ別基礎語彙（新テーマ：Vocabulary / テーマ別語彙）

小学生〜初学者の土台。語彙中心の構成（`vocab` を10〜12語に増やし `grammar_points` は省略可）。

| # | 項目 | lesson id | level | 備考 |
|---|---|---|---|---|
| 44 | 数・序数 | `vocab-numbers` | beginner | 基数/序数/値段・電話番号 |
| 45 | 曜日・月・日付 | `vocab-calendar` | beginner | 曜日/月/日付の言い方 |
| 46 | 色・形 | `vocab-colors-shapes` | beginner | 基本色・形・大小 |
| 47 | 体の部位 | `vocab-body` | beginner | 顔・体・症状とつなげる |
| 48 | 食べ物・飲み物 | `vocab-food` | beginner | 好き嫌い・注文と接続 |
| 49 | 動物・自然 | `vocab-animals` | beginner | 動物・天気・身の回り |
| 50 | 職業・教科 | `vocab-jobs-subjects` | elementary | 将来の夢・学校の教科 |
| 51 | 気持ち・性格の形容詞 | `vocab-feelings` | elementary | happy/tired/kind など |

## バッチK：実用・目的別パック（社会人・受験）

| # | 項目 | lesson id | 推奨テーマ | level | 備考 |
|---|---|---|---|---|---|
| 52 | 自己紹介パック(場面別) | `pack-selfintro` | School / 学校 | beginner | 学校/職場/SNSの型を比較 |
| 53 | 旅行英会話パック | `pack-travel` | Travel / 旅行 | elementary | 出入国〜現地のミニ会話集 |
| 54 | ビジネスメール定型集 | `pack-bizmail` | Business / ビジネス | intermediate | 依頼/謝罪/日程調整（business-04を拡充） |
| 55 | 英検・面接の頻出表現 | `pack-eiken` | Study Skills / 学習スキル | elementary | 面接フレーズ・自己表現 |
| 56 | TOEIC頻出シーン | `pack-toeic` | Business / ビジネス | intermediate | アナウンス/オフィス語彙 |

## バッチL：学習スキルの追加（theme: Study Skills / 学習スキル）

| # | 項目 | lesson id | level | 備考 |
|---|---|---|---|---|
| 57 | 語彙の覚え方 | `skill-vocab-memory` | beginner | 文脈で覚える・復習間隔・単語帳活用 |
| 58 | シャドーイング入門 | `skill-shadowing` | elementary | 手順とコツ（既存speakingの発展） |
| 59 | 多読の進め方 | `skill-extensive-reading` | elementary | レベル選び・辞書を引きすぎない |
| 60 | 学習の習慣化・目標設定 | `skill-habit` | beginner | 1日5分・記録・モチベ維持（進捗機能と連動） |

## 進め方メモ

- 優先度：**F（抜け3本）→ G（基本文法の発展）→ J(基礎語彙) または H(会話シーン)** の順を推奨。
- 新テーマ（Shopping/Health/Pronunciation/Vocabulary/Daily Errands）は `theme`/`theme_ja` を新規付与すれば、`/api/themes` の絞り込みに自動追加される。
- 挿絵は `tools/gen_lesson_svg.py <id> "<Theme>" "<EN>" "<JA>"` で生成（未知テーマは中立モチーフにフォールバック。必要なら同ツールにモチーフを追加）。
- 語彙中心レッスン（バッチJ）は `dialogue`/`grammar_points` を省略可。フロントは存在する項目のみ描画する想定で、`vocab` を厚めにする。

