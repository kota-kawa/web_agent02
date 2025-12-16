# Implementation Summary: Conversation History Check Endpoint

## WebArena Automation (Dec 2025)
- Added WebArena batch runner to execute all locally available environments (Shopping / Shopping Admin / Reddit / GitLab) without manual prompt input (`flask_app/webarena/routes.py`, `flask_app/templates/webarena.html`).
- Tasks are filtered server-side to only supported sites; a new `/webarena/run_batch` endpoint sequentially runs tasks and returns per-task results plus an aggregate score.
- UI updates:
  - Environment filter chips (Shopping / Shopping Admin / Reddit / GitLab) to show only relevant tasks.
  - “表示中タスクを順番に実行” button runs the currently filtered task set sequentially.
  - Removed MAP URL field since that environment is not used.
- Added a screenshot (vision) toggle to WebArena UI that defaults ON and is enforced server-side; only GPT/Gemini/Claude models can receive screenshots (`flask_app/app.py`, `flask_app/templates/webarena.html`, `flask_app/controller.py`, `flask_app/system_prompt.py`).
- Added per-task reset hook: before every WebArena task (single or batch) the browser session is reset and optional external reset hooks (`WEBARENA_RESET_COMMAND` or `WEBARENA_RESET_URL`) are invoked to restore backend state (cart, posts, etc.).
- Extra safety for consecutive runs: when moving to the next WebArena task the agent closes all previously opened tabs and reloads the configured start page so each task starts from a single, refreshed tab (`flask_app/controller.py`, `flask_app/webarena/routes.py`).
- WebArena-only runs now cap the agent at 20 steps (configurable via `WEBARENA_AGENT_MAX_STEPS`, default 20) while the general UI/API uses `AGENT_MAX_STEPS` (default 20).

## Overview
This implementation adds a new endpoint `/api/check-conversation-history` that allows other agents to send conversation history for analysis. The endpoint uses LLM (Gemini) to determine if there are problems that can be solved with browser operations, and automatically executes browser tasks if needed. In addition, the first prompt of `/api/chat` and `/api/agent-relay` is now analyzed to optionally return a text-only reply when browser operations are unnecessary. Conversation context handed to the LLM is trimmed to the very first user input plus the most recent five messages so prompts stay compact while preserving intent.

## Additional Behavior Adjustments
- Browser agent tools now exclude the `read_file` action and the system prompt explicitly forbids it, preventing the LLM from generating or selecting `read_file` tasks (flask_app/controller.py, flask_app/system_prompt_browser_agent.md).
- System prompt strengthens “act-first” guidance so the browser agent proceeds without unnecessary確認質問 when orchestrator-provided tasks are sufficiently clear, using reasonable defaults for general info gathering (flask_app/system_prompt_browser_agent.md).
- System prompt now hard-requires the `action` field with at least one action (fallback to `wait` when unsure) to eliminate validation errors caused by responses that only contained `thinking` (flask_app/system_prompt_browser_agent.md).
- System prompt now treats the timezone-aware `current_datetime` line as authoritative “today” and mandates that time-sensitive searches (weather/news/events/prices) include the current year/month/day to avoid past-year results (flask_app/system_prompt.py, flask_app/system_prompt_browser_agent.md).
- CDP session strategy now defaults to shared sockets per Chrome instance to avoid DevTools hub limits during long WebArena batches. Dedicated per-tab sockets remain opt-in via `BROWSER_USE_DEDICATED_SOCKET_PER_TARGET=true`; failures to open a dedicated socket (HTTP 400 / “Too many websocket connections”) automatically fall back to the shared socket for resilience (browser_use/browser/session.py, crash_watchdog.py, watchdog_base.py).

## Problem Statement (Japanese)
他のエージェントから、会話履歴が送信されてくる時がある。そのために、受け入れるためのエンドポイントを新規で作成してほしい。そして、その会話履歴を確認して、何か問題が発生していて、ブラウザ操作をして解決できそうな場合には、既存のロジックを使って、解決のために操作を実行してほしい。特に何もしなくてよさそうならば、何もしないようにしてほしい。つまり、会話履歴のチェック用のエンドポイントと、それに対するLLMがjsonを出力して、その出力をチェックして処理をするコードを書いてほしい。

Translation: Sometimes conversation history is sent from other agents. Create a new endpoint to receive it. Check the conversation history and if there's a problem that can be solved with browser operations, use existing logic to execute operations to solve it. If nothing needs to be done, do nothing. In other words, create an endpoint for checking conversation history and code that uses LLM to output JSON, then checks and processes that output.

## Implementation Details

### Files Modified

1. **flask_app/app.py** - Main implementation
   - Added `_analyze_conversation_history_async()` function (lines 1735-1837)
   - Added `_analyze_conversation_history()` synchronous wrapper (lines 1840-1846)
   - Added `/api/check-conversation-history` endpoint (lines 1849-1922)
   - Added first-prompt text-only handling for `/api/chat` and `/api/agent-relay` when `needs_action=false`

### Files Created

1. **tests/unit/test_conversation_history_endpoint.py**
   - Test for endpoint existence
   - Test for valid input handling
   - Test for triggering browser actions
   - Test for invalid format rejection

2. **docs/conversation_history_endpoint.md**
   - Comprehensive documentation in Japanese
   - Usage examples
   - API reference
   - Error handling documentation

3. **examples/api/conversation_history_check_example.py**
   - Python script demonstrating how to use the endpoint
   - 5 different example scenarios
   - Proper error handling

## Key Features

### 1. LLM-Based Analysis
The implementation uses Gemini LLM to analyze conversation history and determine:
- **needs_action**: Whether browser operations are needed (boolean)
- **action_type**: Type of action (search, navigate, form_fill, data_extract, etc.)
- **task_description**: Specific task to be performed
- **reason**: Explanation of the decision

### 2. JSON Output Parsing
The LLM is prompted to output JSON, and the implementation includes robust parsing:
- Handles JSON wrapped in markdown code blocks
- Handles raw JSON responses
- Validates structure and provides defaults
- Graceful error handling with informative messages

### 3. Integration with Existing Browser Agent
When action is needed:
- Uses existing `_get_agent_controller()` to get the browser agent
- Executes tasks using the existing `controller.run()` method
- Uses existing `_summarize_history()` to format results
- Maintains compatibility with the existing codebase

### 4. Text-only handling for the first prompt of a task
- `/api/chat` and `/api/agent-relay` run a one-time LLM check on the first prompt of a task.
- If `needs_action=false`, they return only a short text reply (from `analysis.reply` or `reason`) without invoking the browser agent.
- Subsequent prompts follow the existing browser-agent execution flow.

### 5. Error Handling
Comprehensive error handling for:
- Missing or invalid conversation history
- LLM initialization failures
- JSON parsing errors
- Agent already running (returns 409)
- Agent execution failures

### 6. Context window control
- LLM analysis always keeps the first user input and only the latest five conversation messages.
- Older turns are omitted to shrink payloads without losing the original intent.

### 7. Step output enrichment
- UI step summaries now show `現在の状況:` alongside `アクション:` and `次の目標` when the model provides it, improving situational awareness.

## API Specification

### Endpoint: POST /api/check-conversation-history

**Request Body:**
```json
{
  "conversation_history": [
    {"role": "user", "content": "message"},
    {"role": "assistant", "content": "response"}
  ]
}
```

**Response (no action needed):**
```json
{
  "analysis": {
    "needs_action": false,
    "action_type": null,
    "task_description": null,
    "reason": "特に問題は検出されませんでした。"
  },
  "action_taken": false,
  "run_summary": null
}
```

**Response (action taken):**
```json
{
  "analysis": {
    "needs_action": true,
    "action_type": "search",
    "task_description": "Googleで天気を検索する",
    "reason": "ユーザーが天気情報を求めているが、エラーが発生している"
  },
  "action_taken": true,
  "run_summary": "✅ 3ステップでエージェントが実行されました（結果: 成功）。",
  "agent_history": {
    "steps": 3,
    "success": true,
    "final_result": "東京の天気は晴れ、最高気温25度です。"
  }
}
```

**Error Responses:**
- 400: Missing or invalid conversation history
- 409: Agent already running
- 200: Errors during LLM analysis or agent execution (error in response body)

## Design Decisions

### 1. Async/Sync Architecture
- LLM operations are async (`_analyze_conversation_history_async`)
- Flask endpoint is sync, using a wrapper function that creates a new event loop
- This maintains compatibility with Flask's synchronous request handling

### 2. Minimal Changes to Existing Code
- No modifications to existing endpoints or functions
- Only additions at the end of the file
- Reuses existing functions like `_create_gemini_llm()`, `_get_agent_controller()`, and `_summarize_history()`

### 3. Japanese Language Support
- All prompts, error messages, and documentation in Japanese
- Follows the existing codebase convention (which uses Japanese throughout)

### 4. Robust JSON Parsing
- Handles various LLM output formats (with/without markdown)
- Uses regex to extract JSON reliably
- Provides meaningful error messages when parsing fails

## Testing

The test suite includes:
1. **Endpoint existence test** - Verifies the endpoint is accessible
2. **Valid input test** - Tests with proper conversation history (mocked LLM)
3. **Action trigger test** - Verifies browser agent is triggered when needed
4. **Invalid format test** - Ensures proper validation of input

All tests use mocking to avoid dependencies on:
- Real LLM API calls
- Real browser agent execution
- External services

## Usage Example

```python
import requests

conversation = [
    {"role": "user", "content": "東京の天気を教えてください"},
    {"role": "assistant", "content": "エラーが発生しました。"}
]

response = requests.post(
    "http://localhost:5005/api/check-conversation-history",
    json={"conversation_history": conversation}
)

result = response.json()
if result["action_taken"]:
    print(f"Action taken: {result['run_summary']}")
else:
    print(f"No action needed: {result['analysis']['reason']}")
```

## Dependencies

- Flask >= 3.0
- browser-use >= 0.7.8
- python-dotenv >= 1.0
- Gemini API key (GOOGLE_API_KEY or GEMINI_API_KEY environment variable)

## Future Enhancements

Potential improvements for future iterations:
1. Support for streaming responses for long-running tasks
2. Webhook callback when task completes
3. Rate limiting to prevent abuse
4. Authentication/authorization
5. More sophisticated analysis prompts based on conversation context
6. Support for different LLM providers

## Security Considerations

1. No authentication required (should be added for production)
2. LLM responses are sanitized and validated before execution
3. Browser agent operations use existing security measures
4. Input validation prevents malformed requests

---

# Implementation Summary: Scratchpad (外部メモ機能)

## Overview
Scratchpadはエージェントの記憶（Context Window）だけに頼らず、収集した情報を一時保存する「メモ帳」領域です。構造化データを外部に保存し、タスク終了時にそこからまとめて回答を生成できます。

## Problem Statement (Japanese)
情報を保持できず、各個撃破になってしまう問題への対策として、エージェントの記憶（Context Window）だけに頼らず、収集した情報を一時保存する「メモ帳」領域をシステム的に用意します。「店名：〇〇、座敷：あり」といった構造化データを外部に保存し、タスク終了時にそこからまとめて回答を生成させます。

## Implementation Details

### Files Created

1. **browser_use/agent/scratchpad.py** - Scratchpad本体
   - `ScratchpadEntry` - 個別エントリのモデル
   - `Scratchpad` - メモ帳クラス（追加・更新・削除・取得・クリア機能）
   - レポート生成機能（text/markdown/json）
   - 状態のシリアライズ/デシリアライズ

### Files Modified

1. **browser_use/agent/views.py**
   - `AgentState`に`scratchpad: Scratchpad`フィールドを追加
   - Scratchpadのインポートを追加

2. **browser_use/tools/views.py**
   - `ScratchpadAddAction` - 新規エントリ追加
   - `ScratchpadUpdateAction` - 既存エントリ更新
   - `ScratchpadRemoveAction` - エントリ削除
   - `ScratchpadGetAction` - 情報取得
   - `ScratchpadClearAction` - 全削除

3. **browser_use/tools/service.py**
   - Scratchpadアクションの登録（`_register_scratchpad_actions`）
   - `act`メソッドに`scratchpad`パラメータを追加
   - アクションハンドラーでScratchpadを操作

4. **browser_use/agent/service.py**
   - `multi_act`メソッドで`scratchpad=self.state.scratchpad`を渡すように変更

5. **flask_app/system_prompt_browser_agent.md**
   - `<scratchpad>`セクションを追加（使用方法の説明）
   - `<action_schemas>`にScratchpadアクションのスキーマを追加

## Key Features

### 1. 構造化データの保存
```python
scratchpad.add_entry(
    key="店舗A",
    data={"座敷": "あり", "評価": 4.5, "価格帯": "3000-5000円"},
    source_url="https://tabelog.com/...",
    notes="駅から徒歩5分"
)
```

### 2. エントリの更新
```python
scratchpad.update_entry(
    key="店舗A",
    data={"予約": "必要"},  # 既存データにマージ
    merge=True
)
```

### 3. サマリー生成
```python
print(scratchpad.to_summary())
# 出力:
# 【収集データ】（3件）
# 1. 【店舗A】
#   座敷: あり
#   評価: 4.5
# 2. 【店舗B】
#   ...
```

### 4. レポート出力
```python
scratchpad.generate_report(format_type='markdown')
# Markdown形式のレポートを生成
```

## Usage Example

### エージェントでの使用
```json
// Step 1: 店舗Aの情報を収集後
{
  "action": [
    {"scratchpad_add": {
      "key": "店舗A",
      "data": {"座敷": "あり", "評価": 4.2, "価格帯": "3000-5000円"},
      "source_url": "https://tabelog.com/store-a/"
    }}
  ]
}

// Step 2: 店舗Bの情報を収集後
{
  "action": [
    {"scratchpad_add": {
      "key": "店舗B",
      "data": {"座敷": "なし", "評価": 4.5, "価格帯": "2000-4000円"}
    }}
  ]
}

// Step 3: 収集データを確認してタスク完了
{
  "action": [
    {"scratchpad_get": {"key": null}}
  ]
}
// -> 全エントリのサマリーが返される

// Step 4: 最終報告
{
  "action": [
    {"done": {
      "text": "調査結果:\n\n1. 店舗A: 座敷あり、評価4.2、3000-5000円\n2. 店舗B: 座敷なし、評価4.5、2000-4000円\n\n座敷ありで高評価の店舗Aをおすすめします。",
      "success": true
    }}
  ]
}
```

## Design Decisions

### 1. AgentStateへの統合
- Scratchpadは`AgentState`の一部として管理
- エージェントのライフサイクルに連動
- フォローアップタスクでも情報が保持される

### 2. 既存機能との棲み分け
| 機能 | 用途 |
|------|------|
| persistent_notes | 自由形式のメモ（履歴切り捨て後も保持） |
| Scratchpad | 構造化データの収集・比較 |
| file_system | 長文・ファイル出力 |

### 3. アクションの命名
- `scratchpad_`プレフィックスで他のアクションと区別
- 直感的な操作名（add, update, remove, get, clear）

## Future Enhancements

1. Scratchpadの永続化（ファイルシステムへの自動保存）
2. 複数Scratchpadのサポート（タスクごとに分離）
3. テンプレートベースのレポート生成
4. エントリの検索・フィルタリング機能
5. done時に自動的にScratchpadの内容を含める機能
