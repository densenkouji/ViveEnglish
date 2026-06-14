# 配布パッケージ化ガイド（pip不要の配布）

エンドユーザーに `pip` などのコマンドを実行させず、ダブルクリックで使える形にするための方針をまとめます。

## 前提：Foundry Local SDK は「自己完結」

Foundry Local **SDK**（`foundry-local-sdk-winml`）は、推論ランタイム（ONNX Runtime / WinML）を
アプリ内に持ち込みます。公式ドキュメントにも *"The SDK doesn't require the Foundry Local CLI to be
installed... your application is self-contained"*（約20MBの追加）とあり、**別途 Foundry Local 本体
（CLI/MSIX）をインストールしなくても**アプリ内でローカル推論できます。

- ハードウェア対応モジュール（実行プロバイダ＝EP の DLL）と **AIモデル本体**は、初回起動時に
  SDK が自動ダウンロードします。ViveEnglish はこれを**進捗バー付き**で実行します
  （`app/foundry.py` の `ensure_model_async` → `/api/ai/setup`、画面はセットアップ・オーバーレイ）。
- つまり「SDK と必要 DLL を同梱」→「初回だけモデルをDL」という流れになります。

---

## 方式A（推奨）：単一の実行ファイルにする（PyInstaller）

Python も pip も不要の配布物を作ります。**Windowsのビルドマシンで1回だけ**実行します。

```bat
packaging\build_exe.bat
```

これは内部で次を行います。

1. ビルド用の仮想環境を作成
2. `pyinstaller` と実行時依存（FastAPI/uvicorn/openai）＋ `foundry-local-sdk-winml` を導入
3. `packaging\viveenglish.spec` でビルド

成果物は `dist\ViveEnglish\` フォルダ。ユーザーには**このフォルダごと**渡し、中の
`ViveEnglish.exe` をダブルクリックしてもらうだけです（Python・pip 不要）。

spec が同梱するもの：

- Web フロント（`web/`）、レッスン内容（`app/content/`）、挿絵（`web/illustrations/`）
- `foundry_local_sdk` / `onnxruntime` などの**ネイティブDLL・`.node`バイナリ**（`collect_all` で収集）

初回起動でモデルとEPがダウンロードされ、オーバーレイに進捗が出ます。完了後は完全にオフラインで動作します。

> メモ：DB などの書き込みは凍結時 `%LOCALAPPDATA%\ViveEnglish` に保存されます（`app/config.py` が
> `sys.frozen` を検出して切り替え）。`packaging\icon.ico` を置けばアイコンも反映されます。
> ログを見たい場合は spec の `console=True` に変更してください。

---

## 方式B：オフライン用ホイール同梱（Python はある環境向け）

完全な exe 化までは不要だが、ネットワーク制限下でも `pip install` を成功させたい場合。

ビルド側（1回）：

```bat
packaging\make_offline_bundle.bat   REM wheels を vendor\ にダウンロード
```

配布物にプロジェクト一式＋`vendor\` を含め、ユーザー側：

```bat
packaging\run_offline.bat           REM vendor\ から --no-index で導入して起動
```

この方式は **Python が入っている**ことが前提です（pip コマンド自体はスクリプトが代行するので、
ユーザーが手で打つ必要はありません）。

---

## 方式C：開発・動作確認用

```bat
run.bat        REM 仮想環境作成→pip→起動（要・インターネット）
```

---

## チェックリスト（配布前）

- [ ] ビルドマシンの OS／CPU・GPU・NPU 構成が配布先と概ね一致している（EP の互換性）
- [ ] `dist\ViveEnglish\` で `ViveEnglish.exe` が単体起動し、初回DLの進捗が出る
- [ ] 初回DL完了後、ネットワークを切っても翻訳・対話・発話チェックが動く
- [ ] モデルを固定したい場合は環境変数 `VIVE_CHAT_MODEL` を同梱バッチで設定
- [ ] ライセンス：同梱する各モデル／ONNX Runtime／SDK の配布条件を確認

---

## 補足：モデルの初回ダウンロードを制御する環境変数

| 変数 | 既定 | 説明 |
|---|---|---|
| `VIVE_AUTOLOAD_MODEL` | `1` | 初回起動時にモデルを自動DL/ロード（`0`で無効） |
| `VIVE_CHAT_MODEL` | `qwen2.5-1.5b` | DL・利用するチャット（text）モデルのエイリアス |
| `VIVE_MANAGE_FOUNDRY` | `1` | アプリが空きポートでローカルAIを起動 |
| `VIVE_FOUNDRY_PORT` | 自動 | 使用ポートを固定したいとき |

初回DLの進捗は `GET /api/ai/setup-state`（`state`/`progress`/`message`）で取得でき、UIのオーバーレイが購読します。
