"""
Scratchpad - 外部メモ機能

エージェントの記憶（Context Window）だけに頼らず、収集した情報を一時保存する「メモ帳」領域。
構造化データを外部に保存し、タスク終了時にそこからまとめて回答を生成できる。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ScratchpadEntry(BaseModel):
	"""Scratchpad の個別エントリ"""

	key: str = Field(..., description='エントリのキー（例: 店名、項目名）')
	data: dict[str, Any] = Field(default_factory=dict, description='構造化データ')
	source_url: str | None = Field(default=None, description='情報取得元のURL')
	timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description='記録時刻')
	notes: str | None = Field(default=None, description='追加メモ')

	def to_summary(self) -> str:
		"""エントリの要約を生成"""
		parts = [f'【{self.key}】']
		for k, v in self.data.items():
			parts.append(f'  {k}: {v}')
		if self.notes:
			parts.append(f'  メモ: {self.notes}')
		return '\n'.join(parts)


class Scratchpad(BaseModel):
	"""
	外部メモ（Scratchpad）

	エージェントが収集した情報を一時保存し、タスク終了時にまとめて回答を生成するためのシステム。

	使用例:
	- 店舗情報の収集: 店名、座敷有無、評価、価格帯など
	- 検索結果の比較: 複数の製品やサービスの比較情報
	- マルチステップタスク: 各ステップで収集した情報の蓄積
	"""

	entries: list[ScratchpadEntry] = Field(default_factory=list, description='保存されたエントリのリスト')
	task_context: str | None = Field(default=None, description='タスクのコンテキスト情報')
	summary_template: str | None = Field(default=None, description='まとめ生成時のテンプレート')

	def add_entry(
		self,
		key: str,
		data: dict[str, Any],
		source_url: str | None = None,
		notes: str | None = None,
	) -> ScratchpadEntry:
		"""
		新しいエントリを追加

		Args:
		    key: エントリのキー（例: 店名）
		    data: 構造化データ（例: {'座敷': 'あり', '評価': 4.5}）
		    source_url: 情報取得元のURL
		    notes: 追加メモ

		Returns:
		    追加されたエントリ
		"""
		entry = ScratchpadEntry(
			key=key,
			data=data,
			source_url=source_url,
			notes=notes,
		)
		self.entries.append(entry)
		logger.debug(f'Scratchpad: Added entry "{key}" with {len(data)} data fields')
		return entry

	def update_entry(
		self,
		key: str,
		data: dict[str, Any] | None = None,
		notes: str | None = None,
		merge: bool = True,
	) -> ScratchpadEntry | None:
		"""
		既存のエントリを更新

		Args:
		    key: 更新するエントリのキー
		    data: 更新するデータ
		    notes: 更新するメモ
		    merge: Trueの場合、既存データとマージ。Falseの場合、置換。

		Returns:
		    更新されたエントリ、見つからない場合はNone
		"""
		for entry in self.entries:
			if entry.key == key:
				if data is not None:
					if merge:
						entry.data.update(data)
					else:
						entry.data = data
				if notes is not None:
					entry.notes = notes
				entry.timestamp = datetime.now().isoformat()
				logger.debug(f'Scratchpad: Updated entry "{key}"')
				return entry
		return None

	def get_entry(self, key: str) -> ScratchpadEntry | None:
		"""キーでエントリを取得"""
		for entry in self.entries:
			if entry.key == key:
				return entry
		return None

	def remove_entry(self, key: str) -> bool:
		"""エントリを削除"""
		for i, entry in enumerate(self.entries):
			if entry.key == key:
				self.entries.pop(i)
				logger.debug(f'Scratchpad: Removed entry "{key}"')
				return True
		return False

	def clear(self) -> None:
		"""すべてのエントリをクリア"""
		self.entries.clear()
		logger.debug('Scratchpad: Cleared all entries')

	def get_all_keys(self) -> list[str]:
		"""すべてのエントリキーを取得"""
		return [entry.key for entry in self.entries]

	def count(self) -> int:
		"""エントリ数を取得"""
		return len(self.entries)

	def to_summary(self) -> str:
		"""
		すべてのエントリの要約を生成

		タスク終了時にまとめて回答を生成する際に使用。
		"""
		if not self.entries:
			return '（Scratchpadにデータがありません）'

		parts = []
		if self.task_context:
			parts.append(f'【タスク】{self.task_context}\n')

		parts.append(f'【収集データ】（{len(self.entries)}件）\n')

		for i, entry in enumerate(self.entries, 1):
			parts.append(f'{i}. {entry.to_summary()}')
			parts.append('')  # 空行

		return '\n'.join(parts).strip()

	def to_structured_data(self) -> list[dict[str, Any]]:
		"""すべてのエントリを構造化データとして取得"""
		return [
			{
				'key': entry.key,
				'data': entry.data,
				'source_url': entry.source_url,
				'timestamp': entry.timestamp,
				'notes': entry.notes,
			}
			for entry in self.entries
		]

	def to_json(self) -> str:
		"""JSON形式でエクスポート"""
		return json.dumps(self.to_structured_data(), ensure_ascii=False, indent=2)

	@classmethod
	def from_json(cls, json_str: str) -> Scratchpad:
		"""JSONからインポート"""
		data = json.loads(json_str)
		scratchpad = cls()
		for item in data:
			scratchpad.add_entry(
				key=item['key'],
				data=item.get('data', {}),
				source_url=item.get('source_url'),
				notes=item.get('notes'),
			)
		return scratchpad

	def generate_report(self, format_type: str = 'text') -> str:
		"""
		収集データからレポートを生成

		Args:
		    format_type: 'text', 'markdown', 'json'のいずれか

		Returns:
		    生成されたレポート
		"""
		if format_type == 'json':
			return self.to_json()

		if not self.entries:
			return '収集されたデータはありません。'

		if format_type == 'markdown':
			return self._generate_markdown_report()

		return self._generate_text_report()

	def _generate_text_report(self) -> str:
		"""テキスト形式のレポートを生成"""
		lines = []

		if self.task_context:
			lines.append(f'■ タスク: {self.task_context}')
			lines.append('')

		lines.append(f'■ 収集結果 ({len(self.entries)}件)')
		lines.append('=' * 40)

		for i, entry in enumerate(self.entries, 1):
			lines.append(f'\n{i}. {entry.key}')
			lines.append('-' * 30)
			for k, v in entry.data.items():
				lines.append(f'   {k}: {v}')
			if entry.notes:
				lines.append(f'   メモ: {entry.notes}')
			if entry.source_url:
				lines.append(f'   出典: {entry.source_url}')

		return '\n'.join(lines)

	def _generate_markdown_report(self) -> str:
		"""Markdown形式のレポートを生成"""
		lines = []

		if self.task_context:
			lines.append(f'# {self.task_context}')
			lines.append('')

		lines.append(f'## 収集結果 ({len(self.entries)}件)')
		lines.append('')

		for i, entry in enumerate(self.entries, 1):
			lines.append(f'### {i}. {entry.key}')
			lines.append('')
			lines.append('| 項目 | 内容 |')
			lines.append('|------|------|')
			for k, v in entry.data.items():
				lines.append(f'| {k} | {v} |')
			if entry.notes:
				lines.append('')
				lines.append(f'> {entry.notes}')
			if entry.source_url:
				lines.append('')
				lines.append(f'*出典: {entry.source_url}*')
			lines.append('')

		return '\n'.join(lines)

	def get_state(self) -> dict[str, Any]:
		"""状態をシリアライズ可能な形式で取得"""
		return {
			'entries': [entry.model_dump() for entry in self.entries],
			'task_context': self.task_context,
			'summary_template': self.summary_template,
		}

	@classmethod
	def from_state(cls, state: dict[str, Any]) -> Scratchpad:
		"""状態から復元"""
		scratchpad = cls(
			task_context=state.get('task_context'),
			summary_template=state.get('summary_template'),
		)
		for entry_data in state.get('entries', []):
			scratchpad.entries.append(ScratchpadEntry(**entry_data))
		return scratchpad
