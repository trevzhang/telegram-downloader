"""看板：Bot 私聊里唯一一条实时消息，汇总当前任务进度、排队列表与上一个结果。

只有一个刷新循环，只在文本变化时编辑；空闲时不产生任何请求。/status 等命令把看板「移到底部」
（删除旧消息、重发一条），任务开始/进度/结束都原地编辑，不再新发进度消息。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tgdl.bot.notifier import Buttons, Notifier
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

REFRESH_BUTTON = ("🔄 刷新", "refresh")
CANCEL_BUTTON = ("🚫 取消当前", "cancel")
TITLE = "📊 tgdl 看板"
IDLE_TEXT = "💤 当前没有任务"
MAX_QUEUED_LINES = 5
NOTIFY_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class DashboardData:
    current: TaskState | None = None
    active: tuple[TaskState, ...] = ()
    snapshot: ProgressSnapshot | None = None
    note: str | None = None  # 临时提示，如扫描阶段限流等待
    last_finished: TaskState | None = None


@dataclass(frozen=True)
class DashboardView:
    text: str
    buttons: Buttons = field(default=())


def _current_lines(data: DashboardData) -> list[str]:
    if data.current is None:
        return [IDLE_TEXT]
    if data.snapshot is not None:
        return [render_progress(data.snapshot)]
    current = data.current
    target = current.channel_title or current.spec.raw_link
    return [f"🔍 任务 #{current.task_id} {STATUS_LABEL[current.status]} {target}…"]


def _queued_lines(data: DashboardData) -> list[str]:
    queued = [s for s in data.active if s.status is TaskStatus.QUEUED]
    if not queued:
        return []
    lines = ["📋 排队中："] + [
        f"  #{s.task_id} {s.channel_title or s.spec.raw_link}" for s in queued[:MAX_QUEUED_LINES]
    ]
    if len(queued) > MAX_QUEUED_LINES:
        lines.append(f"  …另有 {len(queued) - MAX_QUEUED_LINES} 个")
    return lines


def render_dashboard(data: DashboardData) -> DashboardView:
    lines = [TITLE, *_current_lines(data)]
    if data.note:
        lines.append(data.note)
    lines.extend(_queued_lines(data))
    if data.last_finished is not None:
        lines.append("上一个：" + " ".join(render_summary(data.last_finished).splitlines()[:2]))
    buttons: Buttons = (REFRESH_BUTTON, CANCEL_BUTTON) if data.current is not None else (REFRESH_BUTTON,)
    return DashboardView(truncate_text("\n".join(lines), TELEGRAM_MESSAGE_LIMIT - len(ELLIPSIS)), buttons)


class Dashboard:
    def __init__(
        self,
        notifier: Notifier,
        render: Callable[[], DashboardView],
        interval: float,
        *,
        notify_timeout: float = NOTIFY_TIMEOUT_SECONDS,
    ) -> None:
        self._notifier = notifier
        self._render = render
        self._interval = interval
        self._notify_timeout = notify_timeout
        self._message_id: int | None = None
        self._last_text = ""
        self._wake = asyncio.Event()

    @property
    def message_id(self) -> int | None:
        return self._message_id

    def request_refresh(self) -> None:
        """同步唤醒刷新循环（供队列状态变化回调使用）。"""
        self._wake.set()

    async def show(self) -> None:
        """把看板移到聊天底部：删除旧消息并重新发送。"""
        view = self._render()
        if self._message_id is not None:
            await self._call(self._notifier.delete(self._message_id), "删除旧看板")
        self._message_id = await self._call(self._notifier.send(view.text, view.buttons), "发送看板")
        self._last_text = view.text if self._message_id is not None else ""

    async def refresh(self, force: bool = False) -> None:
        """原地编辑看板；文本未变化则不编辑。编辑失败（如消息被删）时改为重新发送。"""
        if self._message_id is None:
            await self.show()
            return
        view = self._render()
        if view.text == self._last_text and not force:
            return
        edited = await self._call(self._notifier.edit(self._message_id, view.text, view.buttons), "编辑看板")
        if edited is None:
            await self.show()
            return
        self._last_text = view.text

    async def run(self) -> None:
        """定期刷新；request_refresh 可提前唤醒。"""
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except TimeoutError:
                pass
            self._wake.clear()
            await self.refresh()

    async def _call(self, coro: Any, what: str) -> Any:
        """带超时执行通知调用；失败只记录警告。成功返回结果（None 结果用 True 代替以区分失败）。"""
        try:
            result = await asyncio.wait_for(coro, self._notify_timeout)
        except Exception as exc:
            log.warning("%s失败：%s", what, exc)
            return None
        return True if result is None else result
