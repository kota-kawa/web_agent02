#!/usr/bin/env python3
"""
Example script demonstrating how to use the conversation history check endpoint.
This script shows how another agent can send conversation history to the web agent
to check if browser operations are needed.
"""

import json
from typing import Any

import requests


def send_conversation_history(
	conversation_history: list[dict[str, str]], base_url: str = 'http://localhost:5005'
) -> dict[str, Any]:
	"""
	Send conversation history to the check endpoint.

	Args:
	    conversation_history: List of conversation messages with 'role' and 'content'
	    base_url: Base URL of the Flask app

	Returns:
	    Response from the server as a dictionary
	"""
	endpoint = f'{base_url}/api/check-conversation-history'

	payload = {'conversation_history': conversation_history}

	try:
		response = requests.post(endpoint, json=payload, headers={'Content-Type': 'application/json'}, timeout=60)

		response.raise_for_status()
		return response.json()

	except requests.exceptions.RequestException as e:
		print(f'Error sending conversation history: {e}')
		return {'error': str(e)}


def example_1_no_problem():
	"""Example: Normal conversation with no problems."""
	print('=' * 80)
	print('Example 1: Normal conversation (no action needed)')
	print('=' * 80)

	conversation = [
		{'role': 'user', 'content': 'こんにちは'},
		{'role': 'assistant', 'content': 'こんにちは。何かお手伝いできることはありますか?'},
		{'role': 'user', 'content': '今日はいい天気ですね'},
		{'role': 'assistant', 'content': 'そうですね。良い一日をお過ごしください。'},
	]

	result = send_conversation_history(conversation)
	print(json.dumps(result, indent=2, ensure_ascii=False))
	print()


def example_2_search_needed():
	"""Example: User needs information that requires a web search."""
	print('=' * 80)
	print('Example 2: Search needed (action should be taken)')
	print('=' * 80)

	conversation = [
		{'role': 'user', 'content': '東京の今日の天気を教えてください'},
		{'role': 'assistant', 'content': '申し訳ございません。天気情報の取得中にエラーが発生しました。'},
		{'role': 'user', 'content': '困ったなぁ。傘が必要か知りたいんです。'},
	]

	result = send_conversation_history(conversation)
	print(json.dumps(result, indent=2, ensure_ascii=False))
	print()


def example_3_form_submission():
	"""Example: User is having trouble with a form submission."""
	print('=' * 80)
	print('Example 3: Form submission problem (action might be needed)')
	print('=' * 80)

	conversation = [
		{'role': 'user', 'content': 'ホテルの予約をしたいのですが'},
		{'role': 'assistant', 'content': 'ホテルの予約ページを開きました。'},
		{'role': 'user', 'content': '予約フォームに入力したんですが、送信ボタンを押してもエラーになります'},
		{'role': 'assistant', 'content': 'エラーが発生しました。フォームの送信に失敗しました。'},
	]

	result = send_conversation_history(conversation)
	print(json.dumps(result, indent=2, ensure_ascii=False))
	print()


def example_4_navigation_needed():
	"""Example: User wants to go to a specific website."""
	print('=' * 80)
	print('Example 4: Navigation needed (action should be taken)')
	print('=' * 80)

	conversation = [
		{'role': 'user', 'content': 'Amazonのサイトを開いてください'},
		{'role': 'assistant', 'content': 'エラー: ページの読み込みに失敗しました。'},
		{'role': 'user', 'content': 'もう一度試してもらえますか?'},
		{'role': 'assistant', 'content': 'エラーが継続しています。'},
	]

	result = send_conversation_history(conversation)
	print(json.dumps(result, indent=2, ensure_ascii=False))
	print()


def example_5_data_extraction():
	"""Example: User needs to extract data from a website."""
	print('=' * 80)
	print('Example 5: Data extraction needed (action should be taken)')
	print('=' * 80)

	conversation = [
		{'role': 'user', 'content': 'ニュースサイトから今日のトップニュースを教えてください'},
		{'role': 'assistant', 'content': 'ニュースサイトにアクセスしようとしましたが、タイムアウトしました。'},
		{'role': 'user', 'content': 'Yahoo!ニュースでもいいので、トップニュースを見てみてください'},
	]

	result = send_conversation_history(conversation)
	print(json.dumps(result, indent=2, ensure_ascii=False))
	print()


if __name__ == '__main__':
	print('\n')
	print('=' * 80)
	print('Conversation History Check Endpoint - Usage Examples')
	print('=' * 80)
	print()
	print('NOTE: These examples will connect to http://localhost:5005')
	print('Make sure the Flask app is running before executing this script.')
	print()
	input('Press Enter to continue...')
	print()

	try:
		# Run all examples
		example_1_no_problem()
		example_2_search_needed()
		example_3_form_submission()
		example_4_navigation_needed()
		example_5_data_extraction()

		print('=' * 80)
		print('All examples completed!')
		print('=' * 80)

	except KeyboardInterrupt:
		print('\n\nExamples interrupted by user.')
	except Exception as e:
		print(f'\n\nError: {e}')
		import traceback

		traceback.print_exc()
