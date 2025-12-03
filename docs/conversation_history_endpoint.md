# 会話履歴チェックエンドポイント

## 概要

他のエージェントから会話履歴を受け取り、問題が発生していてブラウザ操作で解決できそうな場合に、自動的にブラウザエージェントを起動して問題を解決するエンドポイントです。

## エンドポイント

### POST /api/check-conversation-history

会話履歴を受け取り、LLMで分析して必要に応じてブラウザ操作を実行します。

#### リクエスト形式

```json
{
  "conversation_history": [
    {
      "role": "user",
      "content": "東京の天気を教えてください"
    },
    {
      "role": "assistant", 
      "content": "エラーが発生しました。天気情報を取得できませんでした。"
    }
  ]
}
```

#### レスポンス形式

```json
{
  "analysis": {
    "needs_action": true,
    "action_type": "search",
    "task_description": "Googleで東京の天気を検索する",
    "reason": "ユーザーが天気情報を求めているが、エラーが発生しているため"
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

## 動作フロー

1. **会話履歴の受信**: 他のエージェントから会話履歴がJSON形式で送信される
   - LLMへ渡す直前に、最初のユーザー入力と直近5件のメッセージのみを保持するように圧縮
2. **LLM分析**: Gemini APIを使用して会話履歴を分析
   - 問題が発生しているか
   - ブラウザ操作で解決できるか
   - 具体的なタスクとして実行可能か
3. **判断**: LLMがJSON形式で分析結果を返す
   - `needs_action`: ブラウザ操作が必要かどうか
   - `action_type`: アクションのタイプ（search, navigate, form_fill, data_extract等）
   - `task_description`: 実行すべきタスクの説明
   - `reason`: 判断の理由
4. **実行**: `needs_action`がtrueの場合、既存のブラウザエージェントロジックを使用してタスクを実行
5. **結果の返却**: 実行結果をJSON形式で返す

## 実装の詳細

### 主要な関数

#### `_analyze_conversation_history_async(conversation_history)`

会話履歴を非同期でLLM分析します。

- **入力**: 会話履歴のリスト
- **出力**: 分析結果の辞書
- **使用LLM**: Gemini (環境変数 `GOOGLE_API_KEY` または `GEMINI_API_KEY`)

#### `_analyze_conversation_history(conversation_history)`

`_analyze_conversation_history_async`の同期ラッパー関数です。

#### `check_conversation_history()`

Flaskエンドポイントのハンドラー関数です。

### エラーハンドリング

- LLMの初期化に失敗した場合: `needs_action=false`を返し、理由にエラーメッセージを含める
- JSON解析に失敗した場合: `needs_action=false`を返し、理由にエラーメッセージを含める
- エージェントが既に実行中の場合: HTTPステータス409を返す
- 会話履歴が提供されていない場合: HTTPステータス400を返す
- 会話履歴の形式が不正な場合: HTTPステータス400を返す

## 使用例

### 問題が検出されない場合

```bash
curl -X POST http://localhost:5005/api/check-conversation-history \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_history": [
      {"role": "user", "content": "こんにちは"},
      {"role": "assistant", "content": "こんにちは。何かお手伝いできることはありますか?"}
    ]
  }'
```

レスポンス:
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

### 問題が検出されてブラウザ操作が実行される場合

```bash
curl -X POST http://localhost:5005/api/check-conversation-history \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_history": [
      {"role": "user", "content": "東京の天気を教えてください"},
      {"role": "assistant", "content": "エラーが発生しました。"}
    ]
  }'
```

レスポンス:
```json
{
  "analysis": {
    "needs_action": true,
    "action_type": "search",
    "task_description": "Googleで東京の天気を検索する",
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

## テスト

テストは`tests/unit/test_conversation_history_endpoint.py`にあります。

```bash
pytest tests/unit/test_conversation_history_endpoint.py -v
```

## 依存関係

- Flask >= 3.0
- browser-use >= 0.7.8
- python-dotenv >= 1.0
- Gemini API キー（環境変数 `GOOGLE_API_KEY` または `GEMINI_API_KEY`）

## 注意事項

- LLM分析は非同期で実行されますが、エンドポイント自体は同期的にレスポンスを返します
- ブラウザエージェントが既に実行中の場合、新しいタスクは実行されません（HTTPステータス409）
- LLMの応答はJSON形式を想定していますが、マークダウンコードブロックでラップされている場合も正しく解析されます
- UIチャット(`/api/chat`)とエージェントリレー(`/api/agent-relay`)でも、タスクの最初のプロンプトに対して同じLLM判定を行い、`needs_action=false`ならブラウザを起動せず短いテキストだけを返します
- LLMに渡す会話履歴は「最初のユーザー入力」＋「直近5件」に圧縮されます（古い発話はスキップ）
