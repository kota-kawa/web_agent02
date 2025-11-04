#!/usr/bin/env python3
"""
マルチエージェントシステムの使用例

このスクリプトは、エージェント設定モジュールの使用方法を示します。
各エージェントの情報を取得し、タスクに適したエージェントを提案する方法を学ぶことができます。
"""

import sys
from pathlib import Path

# Add parent directory to path to import agent_config
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

from agent_config import (
	get_all_agents,
	get_agent_info,
	get_agent_description,
	get_agent_display_name,
	get_agent_endpoint,
	suggest_agent_for_task,
)


def print_separator(title: str = '') -> None:
	"""Print a section separator."""
	print('\n' + '=' * 60)
	if title:
		print(f'  {title}')
		print('=' * 60)


def example_1_list_all_agents() -> None:
	"""例1: すべてのエージェントをリスト表示"""
	print_separator('例1: すべてのエージェント情報を取得')
	
	agents = get_all_agents()
	print(f'\n利用可能なエージェント数: {len(agents)}\n')
	
	for agent_id, agent_info in agents.items():
		print(f'[{agent_id}] {agent_info.display_name}')
		print(f'  エンドポイント: {agent_info.api_endpoint}')
		print(f'  タイムアウト: {agent_info.timeout}秒')
		print(f'  説明: {agent_info.description[:100]}...')
		print()


def example_2_get_specific_agent() -> None:
	"""例2: 特定のエージェントの詳細情報を取得"""
	print_separator('例2: 特定のエージェント（FAQ）の詳細情報')
	
	agent = get_agent_info('faq')
	if agent:
		print(f'\nエージェントID: {agent.agent_id}')
		print(f'表示名: {agent.display_name}')
		print(f'エンドポイント: {agent.api_endpoint}')
		print(f'タイムアウト: {agent.timeout}秒')
		print(f'\n機能説明:')
		print(agent.description)


def example_3_agent_helpers() -> None:
	"""例3: ヘルパー関数の使用"""
	print_separator('例3: ヘルパー関数の使用')
	
	print('\n■ エージェント表示名の取得:')
	for agent_id in ['browser', 'faq', 'iot']:
		display_name = get_agent_display_name(agent_id)  # type: ignore[arg-type]
		print(f'  {agent_id} → {display_name}')
	
	print('\n■ エージェントエンドポイントの取得:')
	for agent_id in ['browser', 'faq', 'iot']:
		endpoint = get_agent_endpoint(agent_id)  # type: ignore[arg-type]
		print(f'  {agent_id} → {endpoint}')
	
	print('\n■ エージェント説明の取得（IoT）:')
	iot_desc = get_agent_description('iot')
	print(f'  {iot_desc[:200]}...')


def example_4_suggest_agent() -> None:
	"""例4: タスクに基づいたエージェント提案"""
	print_separator('例4: タスクに基づいたエージェント提案')
	
	# テストケース
	test_cases = [
		'家のエアコンの使い方を教えて',
		'IoTデバイスのセンサーデータを取得して',
		'今日の天気をWebで検索して',
		'ラズパイのカメラで写真を撮影して',
		'家電製品の仕様を知りたい',
		'オンラインフォームに情報を入力する',
	]
	
	for task in test_cases:
		suggestions = suggest_agent_for_task(task)
		print(f'\nタスク: {task}')
		print(f'提案エージェント: {", ".join(suggestions)}')
		
		# 最も適したエージェントの詳細を表示
		best_agent_id = suggestions[0]
		best_agent = get_agent_info(best_agent_id)  # type: ignore[arg-type]
		if best_agent:
			print(f'  → {best_agent.display_name} ({best_agent.api_endpoint})')


def example_5_multi_agent_workflow() -> None:
	"""例5: マルチエージェントワークフローの例"""
	print_separator('例5: マルチエージェントワークフローの例')
	
	print('\nシナリオ: ユーザーが「家のエアコンの電源を入れて、使い方も教えて」と依頼')
	
	task = '家のエアコンの電源を入れて、使い方も教えて'
	print(f'\n1. タスク分析: "{task}"')
	
	# タスクに適したエージェントを提案
	suggestions = suggest_agent_for_task(task)
	print(f'\n2. 提案されたエージェント: {", ".join(suggestions)}')
	
	# ワークフロー例
	print('\n3. 実行ワークフロー:')
	
	# IoTエージェントでデバイス制御
	iot_agent = get_agent_info('iot')
	if iot_agent:
		print(f'\n   [ステップ1] {iot_agent.display_name}')
		print(f'   - タスク: エアコンの電源をONにする')
		print(f'   - エンドポイント: {iot_agent.api_endpoint}/api/chat')
		print('   - 実行内容: {"messages": [{"role": "user", "content": "エアコンの電源を入れて"}]}')
	
	# FAQエージェントで使い方を説明
	faq_agent = get_agent_info('faq')
	if faq_agent:
		print(f'\n   [ステップ2] {faq_agent.display_name}')
		print(f'   - タスク: エアコンの使い方を説明')
		print(f'   - エンドポイント: {faq_agent.api_endpoint}/agent_rag_answer')
		print('   - 実行内容: {"question": "エアコンの基本的な使い方を教えて"}')
	
	print('\n   [完了] 両方のエージェントの結果を統合してユーザーに返答')


def main() -> int:
	"""メイン関数"""
	print('\n' + '=' * 60)
	print('  マルチエージェントシステム 使用例')
	print('=' * 60)
	print('\nこのスクリプトは、エージェント設定モジュールの使用方法を示します。')
	
	try:
		# 各例を実行
		example_1_list_all_agents()
		example_2_get_specific_agent()
		example_3_agent_helpers()
		example_4_suggest_agent()
		example_5_multi_agent_workflow()
		
		print_separator('すべての例が完了しました')
		print('\n詳細はMULTI_AGENT_GUIDE.mdを参照してください。')
		return 0
		
	except Exception as e:
		print(f'\n✗ エラーが発生しました: {e}')
		import traceback
		traceback.print_exc()
		return 1


if __name__ == '__main__':
	sys.exit(main())
