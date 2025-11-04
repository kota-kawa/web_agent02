# マルチエージェントシステム構成

このドキュメントでは、web_agent02リポジトリのマルチエージェント機能について説明します。

## 概要

このシステムは、複数の専門エージェントが協調して動作するマルチエージェントプラットフォームです。各エージェントは特定の領域に特化しており、必要に応じて他のエージェントに支援を求めることができます。

## 利用可能なエージェント

### 1. ブラウザエージェント (Browser Agent)
- **エージェントID**: `browser`
- **表示名**: ブラウザエージェント
- **デフォルトエンドポイント**: `http://localhost:5005`
- **タイムアウト**: 120秒

#### 機能
- Web検索と情報収集
- Webページの閲覧と操作
- フォーム入力と送信
- スクリーンショット取得
- 複数タブの管理
- ページからの構造化データ抽出

#### 使用タイミング
- Web上の情報が必要な場合
- オンラインフォームの送信
- Web操作の自動化が必要な場合

#### API エンドポイント
- `POST /api/chat` - ブラウザタスクの実行
- `POST /api/check-conversation-history` - 会話履歴の分析と必要に応じたアクション
- `GET /api/history` - 実行履歴の取得
- `GET /api/agents` - 利用可能なエージェント情報の取得

### 2. 家庭内エージェント (FAQ Agent)
- **エージェントID**: `faq`
- **表示名**: 家庭内エージェント（FAQ）
- **デフォルトエンドポイント**: `http://localhost:5000`
- **タイムアウト**: 30秒

#### 機能
- ナレッジベースへの質問応答（RAG）
- 家電製品の使い方や仕様の説明
- 家庭内のIoTデバイスに関する情報提供
- 過去の会話履歴の分析
- 家庭内の出来事やイベントに関する情報

#### 使用タイミング
- 家電製品の使い方を知りたい場合
- 家庭内のIoTデバイスの情報が必要な場合
- 家庭関連の質問に答える必要がある場合

#### API エンドポイント
- `POST /rag_answer` - ユーザーからの質問に回答（履歴に保存）
- `POST /agent_rag_answer` - 他エージェントからの質問に回答（履歴に保存しない）
- `POST /analyze_conversation` - 会話履歴を分析し、支援が必要か判断
- `GET /conversation_history` - 会話履歴の取得
- `POST /reset_history` - 会話履歴のリセット

#### 接続設定
リポジトリ: https://github.com/kota-kawa/FAQ_Gemini

環境変数で接続先を設定:
```bash
FAQ_GEMINI_API_BASE=http://localhost:5000
FAQ_GEMINI_TIMEOUT=30
```

### 3. IoTエージェント (IoT Agent)
- **エージェントID**: `iot`
- **表示名**: IoTエージェント
- **デフォルトエンドポイント**: `https://iot-agent.project-kk.com`
- **タイムアウト**: 30秒

#### 機能
- IoTデバイスの状態確認
- デバイスの制御（電源ON/OFF、設定変更など）
- センサーデータの取得
- デバイスの登録と管理
- カメラ撮影やLED制御などのハードウェア操作

#### 使用タイミング
- IoTデバイスの操作や状態確認が必要な場合
- センサーデータの取得が必要な場合
- ハードウェア制御が必要な場合

#### API エンドポイント
- `POST /api/chat` - IoTデバイスへの命令実行
- `POST /api/conversations/review` - 会話履歴のレビュー
- `GET /api/devices` - 登録デバイスの一覧取得
- `POST /api/devices/register` - 新規デバイスの登録

#### 接続設定
リポジトリ: https://github.com/kota-kawa/IoT-Agent

環境変数で接続先を設定:
```bash
IOT_AGENT_API_BASE=https://iot-agent.project-kk.com
IOT_AGENT_TIMEOUT=30
```

## マルチエージェント通信

### エージェント間の協調動作

各エージェントは、タスク実行中に他のエージェントの支援が必要な場合、適切なエージェントを選択して質問や依頼を送信できます。

#### 1. エージェント情報の取得

```python
from agent_config import get_all_agents, get_agent_info

# 全エージェントの情報を取得
agents = get_all_agents()

# 特定のエージェントの情報を取得
faq_agent = get_agent_info('faq')
print(faq_agent.description)
print(faq_agent.api_endpoint)
```

#### 2. タスクに適したエージェントの提案

```python
from agent_config import suggest_agent_for_task

# タスクに基づいてエージェントを提案
task = "家のエアコンの使い方を教えて"
suggested_agents = suggest_agent_for_task(task)
# 結果: ['faq', 'iot', 'browser']
```

#### 3. API経由でのエージェント情報取得

```bash
# 全エージェントの情報を取得
curl http://localhost:5005/api/agents

# 特定のエージェントの情報を取得
curl http://localhost:5005/api/agents/faq

# タスクに適したエージェントを提案
curl -X POST http://localhost:5005/api/agents/suggest \
  -H "Content-Type: application/json" \
  -d '{"task": "家のエアコンの使い方を教えて"}'
```

### 会話履歴の共有

エージェント間で会話履歴を共有し、協調して問題解決を行うことができます。

#### ブラウザエージェントへの会話履歴送信

```bash
curl -X POST http://localhost:5005/api/check-conversation-history \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_history": [
      {"role": "user", "content": "今日の天気は？"},
      {"role": "assistant", "content": "申し訳ありません、天気情報を取得できませんでした。"}
    ]
  }'
```

レスポンス:
```json
{
  "analysis": {
    "needs_action": true,
    "action_type": "search",
    "task_description": "今日の天気を検索して取得する",
    "reason": "ユーザーが天気情報を求めているが、まだ提供されていない"
  },
  "action_taken": true,
  "run_summary": "Yahoo!天気で今日の天気を検索し、情報を取得しました。"
}
```

## 環境変数設定

各エージェントの接続先は環境変数で設定できます。`.env`ファイルに以下を追加:

```bash
# ブラウザエージェント
BROWSER_AGENT_API_BASE=http://localhost:5005
BROWSER_AGENT_TIMEOUT=120

# FAQエージェント
FAQ_GEMINI_API_BASE=http://localhost:5000
FAQ_GEMINI_TIMEOUT=30

# IoTエージェント
IOT_AGENT_API_BASE=https://iot-agent.project-kk.com
IOT_AGENT_TIMEOUT=30
```

## 参考リポジトリ

マルチエージェントシステムの設計は、以下のリポジトリを参考にしています：

- **Multi-Agent-Platform**: https://github.com/kota-kawa/Multi-Agent-Platform
  - マルチエージェントオーケストレーションの実装例
  - エージェント間通信のパターン

- **FAQ_Gemini**: https://github.com/kota-kawa/FAQ_Gemini
  - ナレッジベースエージェントの実装
  - RAG（Retrieval-Augmented Generation）の実装例

- **IoT-Agent**: https://github.com/kota-kawa/IoT-Agent
  - IoTデバイス制御エージェントの実装
  - エッジデバイス連携の実装例

## 開発ガイド

### 新しいエージェントの追加

1. `agent_config.py`にエージェント情報を追加
2. `AgentType`に新しいエージェントIDを追加
3. `AGENT_REGISTRY`に新しい`AgentInfo`を追加
4. 環境変数でエンドポイントを設定可能にする

### エージェント選択ロジックのカスタマイズ

`suggest_agent_for_task`関数を修正して、タスクに基づくエージェント選択ロジックをカスタマイズできます。より高度な選択が必要な場合は、LLMを使用した選択も検討してください。
