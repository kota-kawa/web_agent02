"""
エージェント間協調の実装例

このモジュールは、タスク実行中に他のエージェントに支援を求める方法を示します。
実際のプロジェクトでは、この例をベースに各エージェントの実装に組み込むことができます。
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

# Add parent directory to path to import agent_config
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

from agent_config import get_agent_endpoint, get_agent_info, suggest_agent_for_task

logger = logging.getLogger(__name__)


class AgentCommunicator:
	"""
	エージェント間通信を管理するクラス。
	
	タスク実行中に他のエージェントの支援が必要な場合、
	このクラスを使用して適切なエージェントにリクエストを送信できます。
	"""

	@staticmethod
	def request_help_from_faq(question: str) -> Optional[Dict[str, Any]]:
		"""
		FAQエージェントに質問を送信し、回答を取得します。
		
		Args:
		    question: 質問内容
		
		Returns:
		    FAQ エージェントからの回答、またはエラー時はNone
		
		Example:
		    >>> communicator = AgentCommunicator()
		    >>> result = communicator.request_help_from_faq("エアコンの使い方は？")
		    >>> if result:
		    >>>     print(result['answer'])
		"""
		faq_agent = get_agent_info('faq')
		if not faq_agent:
			logger.error('FAQエージェントの情報が見つかりません')
			return None

		url = f'{faq_agent.api_endpoint}/agent_rag_answer'
		payload = {'question': question}

		try:
			req = Request(
				url,
				data=json.dumps(payload).encode('utf-8'),
				headers={'Content-Type': 'application/json'},
			)
			with urlopen(req, timeout=faq_agent.timeout) as response:
				result = json.loads(response.read().decode('utf-8'))
				logger.info(f'FAQエージェントから回答を取得: {question[:50]}...')
				return result
		except Exception as e:
			logger.error(f'FAQエージェントへのリクエストが失敗しました: {e}')
			return None

	@staticmethod
	def request_help_from_iot(command: str) -> Optional[Dict[str, Any]]:
		"""
		IoTエージェントにコマンドを送信し、結果を取得します。
		
		Args:
		    command: IoTデバイスへの命令
		
		Returns:
		    IoTエージェントからの応答、またはエラー時はNone
		
		Example:
		    >>> communicator = AgentCommunicator()
		    >>> result = communicator.request_help_from_iot("エアコンの電源を入れて")
		    >>> if result:
		    >>>     print(result['reply'])
		"""
		iot_agent = get_agent_info('iot')
		if not iot_agent:
			logger.error('IoTエージェントの情報が見つかりません')
			return None

		url = f'{iot_agent.api_endpoint}/api/chat'
		payload = {'messages': [{'role': 'user', 'content': command}]}

		try:
			req = Request(
				url,
				data=json.dumps(payload).encode('utf-8'),
				headers={'Content-Type': 'application/json'},
			)
			with urlopen(req, timeout=iot_agent.timeout) as response:
				result = json.loads(response.read().decode('utf-8'))
				logger.info(f'IoTエージェントから応答を取得: {command[:50]}...')
				return result
		except Exception as e:
			logger.error(f'IoTエージェントへのリクエストが失敗しました: {e}')
			return None

	@staticmethod
	def request_help_from_browser(task: str) -> Optional[Dict[str, Any]]:
		"""
		ブラウザエージェントにタスクを送信し、結果を取得します。
		
		Args:
		    task: ブラウザで実行するタスクの説明
		
		Returns:
		    ブラウザエージェントからの応答、またはエラー時はNone
		
		Example:
		    >>> communicator = AgentCommunicator()
		    >>> result = communicator.request_help_from_browser("今日の天気を調べて")
		    >>> if result:
		    >>>     print(result.get('run_summary'))
		"""
		browser_agent = get_agent_info('browser')
		if not browser_agent:
			logger.error('ブラウザエージェントの情報が見つかりません')
			return None

		url = f'{browser_agent.api_endpoint}/api/chat'
		payload = {'prompt': task, 'new_task': True}

		try:
			req = Request(
				url,
				data=json.dumps(payload).encode('utf-8'),
				headers={'Content-Type': 'application/json'},
			)
			with urlopen(req, timeout=browser_agent.timeout) as response:
				result = json.loads(response.read().decode('utf-8'))
				logger.info(f'ブラウザエージェントから応答を取得: {task[:50]}...')
				return result
		except Exception as e:
			logger.error(f'ブラウザエージェントへのリクエストが失敗しました: {e}')
			return None

	@staticmethod
	def select_and_request_help(task: str) -> Optional[Dict[str, Any]]:
		"""
		タスクの内容に基づいて最適なエージェントを選択し、支援を要請します。
		
		Args:
		    task: 実行したいタスクの説明
		
		Returns:
		    選択されたエージェントからの応答、またはエラー時はNone
		
		Example:
		    >>> communicator = AgentCommunicator()
		    >>> result = communicator.select_and_request_help("家のエアコンの使い方を教えて")
		    >>> # FAQエージェントが自動的に選択され、質問が送信されます
		"""
		# タスクに適したエージェントを提案
		suggestions = suggest_agent_for_task(task)
		if not suggestions:
			logger.warning('タスクに適したエージェントが見つかりませんでした')
			return None

		# 最も適したエージェントに依頼
		best_agent_id = suggestions[0]
		logger.info(f'タスクを{best_agent_id}エージェントに委譲します: {task[:50]}...')

		if best_agent_id == 'faq':
			return AgentCommunicator.request_help_from_faq(task)
		elif best_agent_id == 'iot':
			return AgentCommunicator.request_help_from_iot(task)
		elif best_agent_id == 'browser':
			return AgentCommunicator.request_help_from_browser(task)
		else:
			logger.error(f'未知のエージェントID: {best_agent_id}')
			return None


class MultiAgentTaskExecutor:
	"""
	複数のエージェントを協調させてタスクを実行するクラス。
	
	Example:
	    >>> executor = MultiAgentTaskExecutor()
	    >>> results = executor.execute_multi_agent_task(
	    >>>     "家のエアコンの電源を入れて、使い方も教えて"
	    >>> )
	    >>> for result in results:
	    >>>     print(f"{result['agent']}: {result['summary']}")
	"""

	def __init__(self):
		self.communicator = AgentCommunicator()

	def execute_multi_agent_task(self, task_description: str) -> List[Dict[str, Any]]:
		"""
		複雑なタスクを分析し、複数のエージェントに分散して実行します。
		
		Args:
		    task_description: タスクの説明
		
		Returns:
		    各エージェントからの結果のリスト
		
		実装例:
		    タスク: "家のエアコンの電源を入れて、使い方も教えて"
		    -> IoTエージェント: 電源をON
		    -> FAQエージェント: 使い方を説明
		"""
		results: List[Dict[str, Any]] = []

		# タスクに適したエージェントを提案
		suggested_agents = suggest_agent_for_task(task_description)

		logger.info(f'タスク実行開始: {task_description}')
		logger.info(f'提案されたエージェント: {", ".join(suggested_agents)}')

		# 各エージェントにサブタスクを実行させる
		# 実際の実装では、タスクを分析してサブタスクに分割するLLMを使用することを推奨
		for agent_id in suggested_agents[:2]:  # 上位2つのエージェント
			agent_info = get_agent_info(agent_id)  # type: ignore[arg-type]
			if not agent_info:
				continue

			logger.info(f'{agent_info.display_name}でタスクを実行中...')

			# エージェントにリクエストを送信
			if agent_id == 'faq':
				result = self.communicator.request_help_from_faq(task_description)
			elif agent_id == 'iot':
				result = self.communicator.request_help_from_iot(task_description)
			elif agent_id == 'browser':
				result = self.communicator.request_help_from_browser(task_description)
			else:
				continue

			if result:
				results.append(
					{
						'agent': agent_id,
						'agent_name': agent_info.display_name,
						'task': task_description,
						'result': result,
						'summary': self._extract_summary(agent_id, result),
					}
				)

		return results

	def _extract_summary(self, agent_id: str, result: Dict[str, Any]) -> str:
		"""エージェントの応答から要約を抽出します。"""
		if agent_id == 'faq':
			return result.get('answer', '応答なし')
		elif agent_id == 'iot':
			return result.get('reply', '応答なし')
		elif agent_id == 'browser':
			return result.get('run_summary', '応答なし')
		else:
			return str(result)


def example_usage():
	"""使用例を実行します。"""
	print('=' * 60)
	print('エージェント間協調の実装例')
	print('=' * 60)

	# ログ設定
	logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

	communicator = AgentCommunicator()

	# 例1: FAQエージェントに質問
	print('\n【例1】FAQエージェントに質問')
	print('質問: エアコンの使い方を教えて')
	# 注意: 実際のFAQエージェントが起動している必要があります
	# result = communicator.request_help_from_faq("エアコンの使い方を教えて")
	# if result:
	#     print(f"回答: {result.get('answer')}")

	# 例2: タスクに基づいて自動的にエージェントを選択
	print('\n【例2】タスクに基づいて最適なエージェントを選択')
	tasks = [
		'家のエアコンの使い方を教えて',
		'ラズパイのカメラで写真を撮影して',
		'今日の天気をWebで調べて',
	]

	for task in tasks:
		suggestions = suggest_agent_for_task(task)
		best_agent = get_agent_info(suggestions[0])  # type: ignore[arg-type]
		if best_agent:
			print(f'\nタスク: {task}')
			print(f'→ 選択されたエージェント: {best_agent.display_name}')
			print(f'  エンドポイント: {best_agent.api_endpoint}')

	# 例3: マルチエージェント協調
	print('\n【例3】マルチエージェント協調タスク')
	executor = MultiAgentTaskExecutor()
	complex_task = '家のエアコンの電源を入れて、使い方も教えて'
	print(f'複雑なタスク: {complex_task}')

	# このタスクはIoTエージェントとFAQエージェントの両方が必要
	suggested = suggest_agent_for_task(complex_task)
	print(f'提案されたエージェント順: {", ".join(suggested)}')

	# 実際の実行は各エージェントが起動している必要があります
	# results = executor.execute_multi_agent_task(complex_task)
	# for result in results:
	#     print(f"\n{result['agent_name']}の結果:")
	#     print(f"  {result['summary']}")

	print('\n' + '=' * 60)
	print('注意: 実際にエージェントを呼び出すには、各エージェントが起動している必要があります。')
	print('詳細はMULTI_AGENT_GUIDE.mdを参照してください。')


if __name__ == '__main__':
	example_usage()
