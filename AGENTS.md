# Agent Playbook

## 目的と全体像
- このリポジトリは `browser_use` のフルソースと、Flask ベースのブラウザ操作エージェント UI/API (`flask_app/`) を同梱し、他エージェントからの会話ログを解析してブラウザタスクを自動実行できるようにしています。
- 主要な変更点は `flask_app/controller.py` の `BrowserAgentController` と `flask_app/app.py` の HTTP エンドポイント群です。Gemini ベースの LLM、Chrome DevTools (CDP) セッション、EventBus を常駐させ、SSE で UI に進捗を配信します。
- `IMPLEMENTATION_SUMMARY.md` と `docs/conversation_history_endpoint.md` に最新仕様を残してあるので、フローを変える場合は必ず両方を更新してください。

## ディレクトリ構成の要点
- `browser_use/` : OSS 本体。`agent/`, `controller/`, `browser/`, `llm/`, `tokens/`, `tools/`, `telemetry/`, `mcp/` などに機能が分類されています。既存ロジックに倣い最も近いサブパッケージへ追加してください。
- `flask_app/` : Web サーバー、SSE、静的 UI、Docker `Dockerfile.flask`、`requirements.txt` を含むアプリ本体。`templates/index.html` + `static/css/style.css` が UI です。
- `docs/` : Mintlify 互換ドキュメント。`docs/conversation_history_endpoint.md` は新規エンドポイントの仕様。プレビューは `cd docs && npx mintlify dev` を使用。
- `examples/` : `examples/api/conversation_history_check_example.py` などサンプル集。再利用が基本方針。
- `docker/` とトップレベル `Dockerfile*` : Chrome/VNC コンテナと Flask コンテナのビルド設定。`docker-compose.yml` は `browser-agent` + `browser` + 共有ネットワーク (`MULTI_AGENT_NETWORK`) を前提にしています。
- `bin/` : `setup.sh`, `test.sh`, `lint.sh`。`uv` ベースの開発環境を前提にしており、プロジェクトルートでの実行を想定。
- `system_prompt_browser_agent.md` : Flask アプリが読み込むカスタム system prompt。`flask_app/system_prompt.py` の `_build_custom_system_prompt()` から `max_actions`/`current_datetime` を埋め込みます。

## Runtime/Controller の仕組み (`flask_app/controller.py` + `flask_app/app.py`)
- `BrowserAgentController`
  - CDP URL を検出 (`_resolve_cdp_url`) し、`BrowserSession` を常駐させるスレッド＋イベントループを持ちます。
  - `GOOGLE_API_KEY` or `GEMINI_API_KEY`、`GOOGLE_GEMINI_MODEL`/`GEMINI_MODEL` に基づき `ChatGoogle` を生成し、`Agent` を初期化。日本語出力を強制する `_LANGUAGE_EXTENSION` と system prompt 差し替えをサポート。
  - `max_actions_per_step` は `_DEFAULT_MAX_ACTIONS_PER_STEP` (10)。`BROWSER_DEFAULT_START_URL`/`system_prompt`/`_DEFAULT_EMBED_BROWSER_URL` を使って開始ページをウォームアップ。
  - `enqueue_follow_up`, `pause`, `resume`, `reset`, `ensure_start_page_ready` など状態管理 API を公開し、`_summarize_history` で完了メッセージをまとめます。
  - EventBus (`bubus.EventBus`) + SSE で UI にステップ別ログを配信。`_format_step_plan`, `_format_result` で整形。
- CDP/ブラウザ
  - `BROWSER_USE_CDP_URL` 明示指定推奨。未設定でも `BROWSER_USE_CDP_CANDIDATES` を巡回し、自動発見 → WebDriver セッション掃除まで行う。
  - `EMBED_BROWSER_URL` を `noVNC` に向け、クエリ正規化で `scale=auto` などを強制。Docker Compose では `browser` サービスの 7900 ポートを埋込み iframe に表示。
- 会話解析 (`_analyze_conversation_history_async`)
  - 受け取った履歴を日本語プロンプトに変換し、LLM 応答から JSON を抽出。Markdown コードブロックや整形崩れを許容する正規表現で解析します。
  - パース失敗や LLM 初期化失敗時は `needs_action=false` で安全にフォールバックし、UI/呼び出し元へ理由を返します。

## HTTP API とフロントエンド
- `GET /` : noVNC iframe + チャット UI を表示。初回アクセス時に `BrowserAgentController.ensure_start_page_ready()` でブラウザをウォームアップ。
- `GET /api/history` : 会話履歴 (`_copy_history()`).
- `GET /api/stream` : Server-Sent Events。`MessageBroadcaster` で `message/update/status/reset` を push。
- `POST /api/chat` : UI からの通常実行。`new_task` 指定で履歴を切り替え、`skip_conversation_review` を true にすると会話分析をスキップして即時実行。完了後 `_summarize_history()` を返却し、失敗時も履歴にメッセージを積み、SSE ステータスを更新。
- `POST /api/agent-relay` : 他エージェント用。エージェント稼働中なら `enqueue_follow_up()`、アイドル時は履歴非記録モードで即時実行。返り値に `steps`, `usage` などを含めます。
- `POST /api/reset|pause|resume` : コントローラ状態操作。
- `POST /api/conversations/review` (alias `/api/check-conversation-history`) : 会話履歴 `{"history":[{"role","content"}]}` を受信 → `_analyze_conversation_history()` → 「一言あった方が良い」場合は `should_reply`/`reply`/`addressed_agents` を返し、必要なら `controller.run()` を発火。`analysis`, `action_taken`, `run_summary`, `agent_history` を返す。409 で「実行中」通知。
- すべてのレスポンスに緩い CORS を付与 (`_handle_cors_preflight`, `_set_cors_headers`)。
- `templates/index.html` は日本語 UI、`static/css/style.css`/JS で SSE 接続、思考中インジケータ、Pause/Reset ボタン等を制御。

## LLM・プロンプト関連
- `system_prompt_browser_agent.md` の `{max_actions}` `{current_datetime}` プレースホルダは自動置換されます。編集時は日本語応答ルールや検索ポリシー（Yahoo 強制など）を壊さないこと。
- `flask_app/system_prompt.py` の `_LANGUAGE_EXTENSION` で追加指示を付与。`GOOGLE_GEMINI_TEMPERATURE` が設定されていれば float で渡されます。
- `browser_use/llm` には Anthropic/OpenAI/Groq/Ollama などのクライアントとテストがあります。Gemini 以外を使いたい場合はまずこの層を拡張し、Flask 側で差し替えられるようにする。

## ビルド・実行・検証
- **セットアップ**: `./bin/setup.sh` で uv venv + 依存を全インストール。システム `python3.11` 以上が必須。
- **ローカル起動 (直接)**:
  1. `source .venv/bin/activate` or `uv run`.
  2. `export FLASK_APP=flask_app/app.py` と Chrome CDP URL/LLM キー (`GOOGLE_API_KEY`, `BROWSER_USE_CDP_URL`) を設定。
  3. `uv run flask run --host 0.0.0.0 --port 5005`.
  4. 別途 Chrome (例: Docker `browser` サービス) を立て、`EMBED_BROWSER_URL` を iframe 用 noVNC に合わせる。
- **ローカル起動 (docker compose)**: `docker compose up --build browser-agent browser`。`secrets.env` に鍵/設定を入れ、`MULTI_AGENT_NETWORK` で他サービスと接続。
- **CLI からライブラリを確認**: `uv run browseruse --help`。
- **Lint**: `./bin/lint.sh` → `uv run pre-commit run --all-files` (Ruff fmt/lint, Pyright, Codespell)。
- **テスト**:
  - `./bin/test.sh` は `pytest --numprocesses auto tests/ci` を想定しているので、現状は `browser_use/**/tests` から対象を選んで `uv run pytest browser_use/agent/tests -m "not slow"` のように直接走らせること。
  - 新規テストは `tests/unit/` 相当のパスか、該当モジュール配下 (例: `browser_use/llm/tests/`) に配置し、`pytest.ini` のマーカー (`unit`, `integration`, `slow`) を付与。
  - 会話履歴エンドポイントはモックで完結するテストを `tests/unit/test_conversation_history_endpoint.py`（未作成なら追加）へ配置。`uv run pytest tests/unit/test_conversation_history_endpoint.py -v`.

## ドキュメント・サンプル
- `IMPLEMENTATION_SUMMARY.md` には今回の API 追加の背景が整理されています。挙動を変えたら更新必須。
- `docs/conversation_history_endpoint.md` : API 仕様、エラーハンドリング、curl 例。
- `examples/api/conversation_history_check_example.py` : 5 種類のシナリオを CLI で一括実行。`requests` が必要。Flask サーバー起動後に使う。
- その他 `examples/` 下のサブディレクトリは個別機能 (クラウド連携、MCP、UI、observability 等) ごとに整理。新機能はここに小さな参照実装を置く。

## セキュリティ & 設定
- `secrets.env.example` をコピーして `secrets.env` を作成 (Docker Compose 用)。API キーや OAuth 情報は絶対にコミットしない。
- `BROWSER_USE_LOGGING_LEVEL` は `.env` で `debug` にするとエージェントのトレースが UI/ログに出ます。リリース時は `info` か `result` に戻す。
- Chrome CDP を外部公開する場合はネットワーク ACL で閉じ、`browser` サービスの 9222/4444/7900 を信頼できるホストに限定。
- LLM 応答の JSON は最小限のバリデーションのみなので、追加アクションを実行する箇所では `analysis['task_description']` の sanitize を徹底。必要なら allow-list を拡充する。
- `AgentController` はプロセス終了時に clean up するため `atexit` を使っています。非同期コードを触る際は loop/thread の二重解放を避ける。

## 貢献フローのヒント
- フロントエンド変更時は `flask_app/static/css/style.css` と JS をまとめて確認し、スクリーンショット or 動画を残す。
- API 変更は `docs/` と `examples/` の更新をセットで行い、`AGENTS.md` に運用手順を追記。
- 長期ジョブや外部 API には `BROWSER_USE_LOGGING_LEVEL=debug` でトレースを採取し、`_broadcaster` を使った UI 通知を忘れない。
- PR 前には `lint` → `pytest` → (必要なら) `uv run browseruse --help` で CLI が壊れていないか確認してください。

## Critical Instruction / 重要指示
どれだけ時間とコストをかけてもよいので、確実に目標が達成されるようにしてほしい。
