"""Bot 命令分发：只处理 OWNER 在私聊中发来的以 / 开头的消息。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from telethon import events

from tgdl.bot.commands import HELP_TEXT, CommandError, parse_cancel, parse_dl, split_command
from tgdl.models import TaskState
from tgdl.progress import STATUS_LABEL, ProgressSnapshot, render_progress
from tgdl.task_queue import TaskQueue

log = logging.getLogger(__name__)

SnapshotFn = Callable[[], ProgressSnapshot | None]
COMMAND_PATTERN = r"^/"
INTERNAL_ERROR_REPLY = "⚠️ 内部错误，请查看日志"


class BotHandlers:
    def __init__(self, queue: TaskQueue, current_snapshot: SnapshotFn) -> None:
        self._queue = queue
        self._current_snapshot = current_snapshot

    async def handle_text(self, text: str) -> str:
        try:
            name, args = split_command(text)
            if name == "/dl":
                return self._dl(args)
            if name == "/tasks":
                return self._tasks()
            if name == "/cancel":
                return self._cancel(args)
            if name == "/status":
                return self._status()
            if name in ("/help", "/start"):
                return HELP_TEXT
            return "未知命令，发送 /help 查看用法"
        except CommandError as exc:
            return f"❌ {exc}\n\n发送 /help 查看用法"

    def register(self, bot_client: Any, owner_id: int) -> None:
        # chats=owner_id 限定私聊：OWNER 在群组里发的命令不会触发
        builder = events.NewMessage(from_users=owner_id, chats=owner_id, incoming=True, pattern=COMMAND_PATTERN)

        @bot_client.on(builder)
        async def _on_command(event: Any) -> None:
            log.info("收到命令: %s", event.raw_text)
            try:
                reply = await self.handle_text(event.raw_text)
            except Exception:  # 处理器内部错误不能让 Bot 静默，也不能让异常逃到 Telethon 事件循环
                log.exception("处理命令失败: %s", event.raw_text)
                reply = INTERNAL_ERROR_REPLY
            await event.reply(reply, parse_mode=None)

    def _dl(self, args: list[str]) -> str:
        state = self._queue.submit(parse_dl(args))
        ahead = len(self._queue.active()) - 1
        return f"✅ 已加入队列，任务 #{state.task_id}，前面还有 {ahead} 个任务\n{state.spec.raw_link}"

    def _tasks(self) -> str:
        active = self._queue.active()
        if not active:
            return "当前没有任务"
        return "📋 任务列表\n" + "\n".join(self._format_task(s) for s in active)

    def _format_task(self, state: TaskState) -> str:
        label = STATUS_LABEL[state.status]
        target = state.channel_title or state.spec.raw_link
        return f"#{state.task_id} {label}  {target}{self._task_detail(state)}"

    def _task_detail(self, state: TaskState) -> str:
        """正在执行的任务用实时快照显示 已完成/总数；其余任务只有扫描结果时显示总数。"""
        snapshot = self._current_snapshot()
        current = self._queue.current()
        if snapshot is not None and current is not None and current.task_id == state.task_id:
            return f"  {snapshot.finished_files}/{snapshot.total_files}"
        return f"  共 {len(state.items)} 个" if state.items else ""

    def _cancel(self, args: list[str]) -> str:
        task_id = parse_cancel(args)
        if self._queue.cancel(task_id):
            return f"🚫 任务 #{task_id} 已取消"
        return f"任务 #{task_id} 不存在或已结束"

    def _status(self) -> str:
        snapshot = self._current_snapshot()
        if snapshot is not None:
            return render_progress(snapshot)
        current = self._queue.current()
        if current is not None:
            return f"🔍 任务 #{current.task_id} 正在扫描 {current.channel_title or current.spec.raw_link}…"
        return "当前没有正在下载的任务"
