"""看板：Bot 私聊里唯一一条实时消息，永远原地编辑，按钮在「进度 / 队列 / 历史」视图间切换。

只有一个刷新循环，只在文本变化时编辑；空闲时不产生任何请求。show/refresh 串行执行，避免并发时重复发送。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from telethon.errors import MessageNotModifiedError

from tgdl.bot.notifier import ButtonRow, Buttons, Notifier
from tgdl.models import TaskState, TaskStatus
from tgdl.progress import (
    ELLIPSIS,
    STATUS_LABEL,
    TELEGRAM_MESSAGE_LIMIT,
    ProgressSnapshot,
    render_progress,
    render_summary,
    truncate_text,
)

log = logging.getLogger(__name__)

VIEW_PROGRESS, VIEW_QUEUE, VIEW_HISTORY = "progress", "queue", "history"
VIEW_PREFIX = "view:"
VIEWS: tuple[tuple[str, str], ...] = ((VIEW_PROGRESS, "📥 进度"), (VIEW_QUEUE, "📋 队列"), (VIEW_HISTORY, "📜 历史"))
REFRESH_BUTTON = ("🔄 刷新", "refresh")
CANCEL_BUTTON = ("🚫 取消当前", "cancel")
ACTION_ROW_IDLE: ButtonRow = (REFRESH_BUTTON,)
ACTION_ROW_RUNNING: ButtonRow = (REFRESH_BUTTON, CANCEL_BUTTON)
TITLE = "📊 tgdl 看板"
IDLE_TEXT = "💤 当前没有任务"
NO_HISTORY_TEXT = "还没有结束的任务"
HISTORY_LIMIT = 5
NOTIFY_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class DashboardData:
    current: TaskState | None = None
    active: tuple[TaskState, ...] = ()
    snapshot: ProgressSnapshot | None = None
    note: str | None = None  # 临时提示，如扫描阶段限流等待
    finished: tuple[TaskState, ...] = ()  # 最近结束的任务，最新在前


@dataclass(frozen=True)
class DashboardView:
    text: str
    buttons: Buttons


def view_row(active: str) -> ButtonRow:
    return tuple((f"• {label}" if key == active else label, VIEW_PREFIX + key) for key, label in VIEWS)


def _task_line(state: TaskState, snapshot: ProgressSnapshot | None) -> str:
    target = state.channel_title or state.spec.raw_link
    progress = (
        f"  {snapshot.finished_files}/{snapshot.total_files}" if snapshot and snapshot.task_id == state.task_id else ""
    )
    return f"任务 #{state.task_id} {STATUS_LABEL[state.status]}  {target}{progress}"


def _progress_lines(data: DashboardData) -> list[str]:
    if data.current is None:
        lines = [IDLE_TEXT]
        if data.finished:
            lines.append("上一个：" + " ".join(render_summary(data.finished[0]).splitlines()[:2]))
        return lines
    if data.snapshot is not None:
        return [render_progress(data.snapshot)]
    return [f"🔍 {_task_line(data.current, None)}…"]


def _queue_lines(data: DashboardData) -> list[str]:
    if not data.active:
        return [IDLE_TEXT]
    return ["📋 任务列表"] + [_task_line(s, data.snapshot) for s in data.active]


def _history_lines(data: DashboardData) -> list[str]:
    if not data.finished:
        return [NO_HISTORY_TEXT]
    return ["📜 最近结束"] + [" ".join(render_summary(s).splitlines()[:2]) for s in data.finished[:HISTORY_LIMIT]]


_RENDERERS: dict[str, Callable[[DashboardData], list[str]]] = {
    VIEW_PROGRESS: _progress_lines,
    VIEW_QUEUE: _queue_lines,
    VIEW_HISTORY: _history_lines,
}


def render_dashboard(data: DashboardData, view: str) -> DashboardView:
    lines = [TITLE, *_RENDERERS[view](data)]
    if data.note:
        lines.append(data.note)
    if data.current is not None and data.current.status is TaskStatus.QUEUED:
        lines.append("⏳ 等待开始")
    actions = ACTION_ROW_RUNNING if data.current is not None else ACTION_ROW_IDLE
    text = truncate_text("\n".join(lines), TELEGRAM_MESSAGE_LIMIT - len(ELLIPSIS))
    return DashboardView(text, (view_row(view), actions))


class Dashboard:
    def __init__(
        self,
        notifier: Notifier,
        data: Callable[[], DashboardData],
        interval: float,
        *,
        notify_timeout: float = NOTIFY_TIMEOUT_SECONDS,
    ) -> None:
        self._notifier = notifier
        self._data = data
        self._interval = interval
        self._notify_timeout = notify_timeout
        self._view = VIEW_PROGRESS
        self._message_id: int | None = None
        self._last_text = ""
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def message_id(self) -> int | None:
        return self._message_id

    @property
    def view(self) -> str:
        return self._view

    def request_refresh(self) -> None:
        """同步唤醒刷新循环（供队列状态变化回调使用）。"""
        self._wake.set()

    async def set_view(self, view: str) -> None:
        if view in _RENDERERS:
            self._view = view
        await self.refresh(force=True)

    async def refresh(self, force: bool = False) -> None:
        """原地编辑看板；文本未变化则不编辑。没有看板或编辑失败（如消息被删）时重新发送一条。"""
        async with self._lock:
            view = render_dashboard(self._data(), self._view)
            if self._message_id is None:
                await self._send(view)
                return
            if view.text == self._last_text and not force:
                return
            if await self._edit(self._message_id, view):
                self._last_text = view.text
            else:
                await self._send(view)

    async def run(self) -> None:
        """定期刷新；request_refresh 可提前唤醒。"""
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except TimeoutError:
                pass
            self._wake.clear()
            await self.refresh()

    async def _send(self, view: DashboardView) -> None:
        if self._message_id is not None:
            await self._call(self._notifier.delete(self._message_id), "删除旧看板")
        self._message_id = await self._call(self._notifier.send(view.text, view.buttons), "发送看板")
        self._last_text = view.text if self._message_id is not None else ""

    async def _edit(self, message_id: int, view: DashboardView) -> bool:
        try:
            await asyncio.wait_for(self._notifier.edit(message_id, view.text, view.buttons), self._notify_timeout)
        except MessageNotModifiedError:
            return True
        except Exception as exc:
            log.warning("编辑看板失败，将重新发送：%s", exc)
            return False
        return True

    async def _call(self, coro: Any, what: str) -> Any:
        try:
            return await asyncio.wait_for(coro, self._notify_timeout)
        except Exception as exc:
            log.warning("%s失败：%s", what, exc)
            return None
