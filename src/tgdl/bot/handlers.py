"""Bot 命令分发：只处理 OWNER 发来的以 / 开头的消息。"""
from __future__ import annotations

import logging
from typing import Any, Callable

from telethon import events

from tgdl.bot.commands import HELP_TEXT, CommandError, parse_cancel, parse_dl, split_command
from tgdl.models import TaskState
from tgdl.progress import STATUS_LABEL, ProgressSnapshot, render_progress
from tgdl.task_queue import TaskQueue

log = logging.getLogger(__name__)

SnapshotFn = Callable[[], ProgressSnapshot | None]
COMMAND_PATTERN = r"^/"


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
        @bot_client.on(events.NewMessage(from_users=owner_id, incoming=True, pattern=COMMAND_PATTERN))
        async def _on_command(event: Any) -> None:
            log.info("收到命令: %s", event.raw_text)
            await event.reply(await self.handle_text(event.raw_text))

    def _dl(self, args: list[str]) -> str:
        state = self._queue.submit(parse_dl(args))
        position = len(self._queue.active())
        return f"✅ 已加入队列，任务 #{state.task_id}（队列位置 {position}）\n{state.spec.raw_link}"

    def _tasks(self) -> str:
        active = self._queue.active()
        if not active:
            return "当前没有任务"
        return "📋 任务列表\n" + "\n".join(self._format_task(s) for s in active)

    def _format_task(self, state: TaskState) -> str:
        label = STATUS_LABEL[state.status]
        target = state.channel_title or state.spec.raw_link
        detail = f"  {len(state.results)}/{len(state.items)}" if state.items else ""
        return f"#{state.task_id} {label}  {target}{detail}"

    def _cancel(self, args: list[str]) -> str:
        task_id = parse_cancel(args)
        if self._queue.cancel(task_id):
            return f"🚫 任务 #{task_id} 已取消"
        return f"任务 #{task_id} 不存在或已结束"

    def _status(self) -> str:
        snapshot = self._current_snapshot()
        if snapshot is None:
            return "当前没有正在下载的任务"
        return render_progress(snapshot)
