# マルチエージェント機能の追加説明

## 概要

このセクションでは、web_agent02に追加されたマルチエージェント機能について説明します。

## 追加されたファイル

### 1. `agent_config.py`
エージェント設定モジュール。各エージェントの役割、機能、エンドポイント情報を管理します。

```python
from agent_config import get_all_agents, suggest_agent_for_task

# すべてのエージェント情報を取得
agents = get_all_agents()

# タスクに適したエージェントを提案
suggestions = suggest_agent_for_task("家のエアコンの使い方を教えて")
# 結果: ['faq', 'iot', 'browser']
```

### 2. `MULTI_AGENT_GUIDE.md`
マルチエージェントシステムの完全なガイド。各エージェントの詳細な説明、APIエンドポイント、使用例が含まれています。

### 3. `examples/multi_agent_example.py`
マルチエージェント機能の使用例を示すスクリプト。

実行方法:
```bash
python examples/multi_agent_example.py
```

### 4. `test_agent_config.py`
エージェント設定モジュールのテストスイート。

実行方法:
```bash
python test_agent_config.py
```

## 新しいAPIエンドポイント

Flask appに以下のエンドポイントが追加されました:

### `GET /api/agents`
利用可能なすべてのエージェントの情報を取得

```bash
curl http://localhost:5005/api/agents
```

### `GET /api/agents/<agent_id>`
特定のエージェントの詳細情報を取得

```bash
curl http://localhost:5005/api/agents/faq
```

### `POST /api/agents/suggest`
タスクに基づいて最適なエージェントを提案

```bash
curl -X POST http://localhost:5005/api/agents/suggest \
  -H "Content-Type: application/json" \
  -d '{"task": "IoTデバイスの状態を確認して"}'
```

## 設定されたエージェント

### 1. ブラウザエージェント (Browser Agent)
- **ID**: `browser`
- **役割**: Web情報の収集とブラウザ操作の自動化
- **エンドポイント**: `http://localhost:5005` (環境変数: `BROWSER_AGENT_API_BASE`)

### 2. 家庭内エージェント (FAQ Agent)
- **ID**: `faq`
- **役割**: 家電製品とIoTデバイスに関する知識提供
- **エンドポイント**: `http://localhost:5000` (環境変数: `FAQ_GEMINI_API_BASE`)
- **リポジトリ**: https://github.com/kota-kawa/FAQ_Gemini

### 3. IoTエージェント (IoT Agent)
- **ID**: `iot`
- **役割**: IoTデバイスの制御とセンサーデータの取得
- **エンドポイント**: `https://iot-agent.project-kk.com` (環境変数: `IOT_AGENT_API_BASE`)
- **リポジトリ**: https://github.com/kota-kawa/IoT-Agent

## 環境変数の設定

`.env`ファイルに以下の設定を追加することで、各エージェントの接続先をカスタマイズできます:

```bash
# ブラウザエージェント
BROWSER_AGENT_API_BASE=http://localhost:5005
BROWSER_AGENT_TIMEOUT=120

# FAQエージェント (家庭内エージェント)
FAQ_GEMINI_API_BASE=http://localhost:5000
FAQ_GEMINI_TIMEOUT=30

# IoTエージェント
IOT_AGENT_API_BASE=https://iot-agent.project-kk.com
IOT_AGENT_TIMEOUT=30
```

## 使用例

### エージェント情報の取得

```python
from agent_config import get_agent_info

# FAQエージェントの情報を取得
faq_agent = get_agent_info('faq')
print(f"表示名: {faq_agent.display_name}")
print(f"エンドポイント: {faq_agent.api_endpoint}")
print(f"説明: {faq_agent.description}")
```

### タスクに基づくエージェント選択

```python
from agent_config import suggest_agent_for_task

# IoT関連のタスク
task = "ラズパイのカメラで写真を撮影して"
suggestions = suggest_agent_for_task(task)
print(f"推奨エージェント: {suggestions}")  # ['iot', ...]

# Web検索タスク
task = "今日の天気を調べて"
suggestions = suggest_agent_for_task(task)
print(f"推奨エージェント: {suggestions}")  # ['browser', ...]
```

### マルチエージェント協調の例

```python
from agent_config import suggest_agent_for_task, get_agent_info

# 複合的なタスク
task = "家のエアコンの電源を入れて、使い方も教えて"

# 適切なエージェントを提案
suggestions = suggest_agent_for_task(task)

# 各エージェントに順次リクエスト
for agent_id in suggestions[:2]:  # 上位2つのエージェント
    agent = get_agent_info(agent_id)
    print(f"{agent.display_name}を使用: {agent.api_endpoint}")
    # ここで実際のAPIリクエストを実行
```

## 参考資料

- 詳細なガイド: [MULTI_AGENT_GUIDE.md](MULTI_AGENT_GUIDE.md)
- 使用例スクリプト: [examples/multi_agent_example.py](examples/multi_agent_example.py)
- Multi-Agent-Platformリポジトリ: https://github.com/kota-kawa/Multi-Agent-Platform
