"""实时消息：把 /status、/tasks 等回复登记起来，由统一循环定期重新渲染并编辑，直到内容标记为结束。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from tgdl.bot.notifier import Notifier

log = logging.getLogger(__name__)

Render = Callable[[], tuple[str, bool]]  # 返回 (最新文本, 是否已结束)；结束后做最后一次编辑并停止刷新
MAX_LIVE_MESSAGES = 5  # 同时刷新的消息上限，超出时丢弃最早登记的，避免触发编辑频率限制
EDIT_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class _Entry:
    message_id: int
    render: Render
    last_text: str


class LiveMessages:
    def __init__(self, notifier: Notifier, interval: float, *, edit_timeout: float = EDIT_TIMEOUT_SECONDS) -> None:
        self._notifier = notifier
        self._interval = interval
        self._edit_timeout = edit_timeout
        self._entries: tuple[_Entry, ...] = ()

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def message_ids(self) -> tuple[int, ...]:
        return tuple(e.message_id for e in self._entries)

    def register(self, message_id: int, render: Render, *, initial_text: str) -> None:
        entries = (*self._entries, _Entry(message_id, render, initial_text))
        self._entries = entries[-MAX_LIVE_MESSAGES:]

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.tick()

    async def tick(self) -> None:
        kept: list[_Entry] = []
        for entry in self._entries:
            updated = await self._refresh(entry)
            if updated is not None:
                kept.append(updated)
        self._entries = tuple(kept)

    async def _refresh(self, entry: _Entry) -> _Entry | None:
        """重新渲染并按需编辑；返回 None 表示该消息不再刷新。"""
        try:
            text, done = entry.render()
        except Exception as exc:
            log.warning("实时消息 %s 渲染失败，停止刷新：%s", entry.message_id, exc)
            return None
        if text != entry.last_text:
            await self._edit(entry.message_id, text)
        return None if done else replace(entry, last_text=text)

    async def _edit(self, message_id: int, text: str) -> None:
        try:
            await asyncio.wait_for(self._notifier.edit(message_id, text), self._edit_timeout)
        except Exception as exc:
            log.warning("编辑实时消息 %s 失败：%s", message_id, exc)
