"""
Agent configuration module for multi-agent communication.

This module defines available agents, their roles, capabilities, and connection endpoints.
Agents can use this configuration to discover and communicate with other specialized agents.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

# Agent type definitions
AgentType = Literal['browser', 'faq', 'iot']


@dataclass
class AgentInfo:
	"""Information about an agent including its role, capabilities, and endpoints."""

	# Agent identifier (used in code)
	agent_id: AgentType
	# Human-readable display name (preferably in Japanese for this project)
	display_name: str
	# Detailed description of the agent's role and capabilities
	description: str
	# Primary API endpoint for communication
	api_endpoint: str
	# Timeout for API calls in seconds
	timeout: float = 30.0


# Agent registry - defines all available agents in the system
AGENT_REGISTRY: dict[AgentType, AgentInfo] = {
	'browser': AgentInfo(
		agent_id='browser',
		display_name='ブラウザエージェント',
		description=(
			'Webブラウザを自動操作して情報収集やタスクを実行するエージェント。\n'
			'機能:\n'
			'- Web検索と情報収集\n'
			'- Webページの閲覧と操作\n'
			'- フォーム入力と送信\n'
			'- スクリーンショット取得\n'
			'- 複数タブの管理\n'
			'- ページからの構造化データ抽出\n'
			'使用タイミング: Web上の情報が必要な場合、オンラインフォームの送信、'
			'Web操作の自動化が必要な場合'
		),
		api_endpoint=os.environ.get('BROWSER_AGENT_API_BASE', 'http://localhost:5005'),
		timeout=float(os.environ.get('BROWSER_AGENT_TIMEOUT', '120')),
	),
	'faq': AgentInfo(
		agent_id='faq',
		display_name='家庭内エージェント（FAQ）',
		description=(
			'家庭内の出来事や家電製品に関する専門知識を持つエージェント。\n'
			'機能:\n'
			'- ナレッジベースへの質問応答（RAG）\n'
			'- 家電製品の使い方や仕様の説明\n'
			'- 家庭内のIoTデバイスに関する情報提供\n'
			'- 過去の会話履歴の分析\n'
			'- 家庭内の出来事やイベントに関する情報\n'
			'使用タイミング: 家電製品の使い方、家庭内のIoTデバイスの情報、'
			'家庭関連の質問に答える必要がある場合'
		),
		api_endpoint=os.environ.get('FAQ_GEMINI_API_BASE', 'http://localhost:5000'),
		timeout=float(os.environ.get('FAQ_GEMINI_TIMEOUT', '30')),
	),
	'iot': AgentInfo(
		agent_id='iot',
		display_name='IoTエージェント',
		description=(
			'IoTデバイスの制御と状態確認を行うエージェント。\n'
			'機能:\n'
			'- IoTデバイスの状態確認\n'
			'- デバイスの制御（電源ON/OFF、設定変更など）\n'
			'- センサーデータの取得\n'
			'- デバイスの登録と管理\n'
			'- カメラ撮影やLED制御などのハードウェア操作\n'
			'使用タイミング: IoTデバイスの操作や状態確認が必要な場合、'
			'センサーデータの取得、ハードウェア制御が必要な場合'
		),
		api_endpoint=os.environ.get(
			'IOT_AGENT_API_BASE', 'https://iot-agent.project-kk.com'
		),
		timeout=float(os.environ.get('IOT_AGENT_TIMEOUT', '30')),
	),
}


def get_agent_info(agent_id: AgentType) -> AgentInfo | None:
	"""
	Get agent information by agent ID.

	Args:
	    agent_id: The agent identifier

	Returns:
	    AgentInfo object or None if agent not found
	"""
	return AGENT_REGISTRY.get(agent_id)


def get_all_agents() -> dict[AgentType, AgentInfo]:
	"""
	Get all registered agents.

	Returns:
	    Dictionary of all agent configurations
	"""
	return AGENT_REGISTRY.copy()


def get_agent_description(agent_id: AgentType) -> str:
	"""
	Get a human-readable description of an agent's capabilities.

	Args:
	    agent_id: The agent identifier

	Returns:
	    Description string or empty string if agent not found
	"""
	agent = get_agent_info(agent_id)
	return agent.description if agent else ''


def get_agent_display_name(agent_id: AgentType) -> str:
	"""
	Get the display name of an agent.

	Args:
	    agent_id: The agent identifier

	Returns:
	    Display name or the agent_id if not found
	"""
	agent = get_agent_info(agent_id)
	return agent.display_name if agent else str(agent_id)


def get_agent_endpoint(agent_id: AgentType) -> str | None:
	"""
	Get the API endpoint for an agent.

	Args:
	    agent_id: The agent identifier

	Returns:
	    API endpoint URL or None if agent not found
	"""
	agent = get_agent_info(agent_id)
	return agent.api_endpoint if agent else None


def suggest_agent_for_task(task_description: str) -> list[AgentType]:
	"""
	Suggest which agent(s) might be best suited for a given task.

	This is a simple heuristic-based suggestion. For more sophisticated
	agent selection, consider using an LLM-based approach.

	Args:
	    task_description: Description of the task to perform

	Returns:
	    List of suggested agent IDs in priority order
	"""
	task_lower = task_description.lower()
	suggestions: list[AgentType] = []

	# Check for IoT-related keywords
	iot_keywords = [
		'iot',
		'デバイス',
		'device',
		'センサー',
		'sensor',
		'カメラ',
		'camera',
		'led',
		'制御',
		'control',
		'電源',
		'power',
		'ラズパイ',
		'raspberry',
		'jetson',
	]
	if any(keyword in task_lower for keyword in iot_keywords):
		suggestions.append('iot')

	# Check for FAQ/knowledge base keywords
	faq_keywords = [
		'家電',
		'家庭',
		'使い方',
		'仕様',
		'説明',
		'知識',
		'knowledge',
		'faq',
		'質問',
		'question',
		'教えて',
		'how to',
	]
	if any(keyword in task_lower for keyword in faq_keywords):
		suggestions.append('faq')

	# Check for browser/web keywords
	browser_keywords = [
		'web',
		'ブラウザ',
		'browser',
		'検索',
		'search',
		'サイト',
		'site',
		'ページ',
		'page',
		'url',
		'http',
		'www',
		'オンライン',
		'online',
		'インターネット',
		'internet',
	]
	if any(keyword in task_lower for keyword in browser_keywords):
		suggestions.append('browser')

	# If no specific keywords found, return all agents in default order
	if not suggestions:
		suggestions = ['browser', 'faq', 'iot']

	return suggestions
