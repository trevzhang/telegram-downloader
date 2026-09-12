"""按固定间隔把进度文本写回同一条 Bot 消息，去重并容忍编辑失败。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from telethon.errors import MessageNotModifiedError

log = logging.getLogger(__name__)

DEFAULT_INTERVAL = 5.0

EditFn = Callable[[str], Awaitable[None]]


class ProgressReporter:
    def __init__(self, edit: EditFn, interval: float = DEFAULT_INTERVAL) -> None:
        self._edit = edit
        self._interval = interval
        self._last_text = ""

    async def update(self, text: str) -> bool:
        """文本变化时才编辑；返回是否真正发送了编辑。

        任何 Exception（RPCError、网络错误等）都只记录警告，不中断刷新循环；
        CancelledError 是 BaseException，会正常向上传播。
        """
        if text == self._last_text:
            return False
        try:
            await self._edit(text)
        except MessageNotModifiedError:
            self._last_text = text
            return False
        except Exception as exc:
            log.warning("编辑进度消息失败: %s", exc)
            return False
        self._last_text = text
        return True

    async def run(self, render: Callable[[], str], stop: asyncio.Event) -> None:
        """循环刷新直到 stop 被置位，最后强制刷新一次。"""
        while not stop.is_set():
            await self.update(render())
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue
        await self.update(render())
