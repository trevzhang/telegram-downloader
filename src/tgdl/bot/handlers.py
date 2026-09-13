"""Bot 命令与按钮分发：只处理 OWNER 在私聊中的消息；状态展示一律落在看板上，不新发进度消息。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from telethon import events

from tgdl.bot.commands import HELP_TEXT, CommandError, parse_cancel, parse_dl, split_command
from tgdl.bot.dashboard import VIEW_PREFIX
from tgdl.task_queue import TaskQueue

log = logging.getLogger(__name__)

COMMAND_PATTERN = r"^/"
INTERNAL_ERROR_REPLY = "⚠️ 内部错误，请查看日志"
REFRESHED_ANSWER = "已刷新"
NO_CURRENT_TASK_ANSWER = "没有正在执行的任务"
UNKNOWN_BUTTON_ANSWER = "未知操作"
BUTTON_REFRESH = "refresh"
BUTTON_CANCEL = "cancel"


@dataclass(frozen=True)
class Reply:
    text: str | None
    move_dashboard: bool = False  # 命令处理后把看板移到聊天底部（删旧发新）


class DashboardLike(Protocol):
    async def show(self) -> None: ...
    async def refresh(self, force: bool = False) -> None: ...
    async def set_view(self, view: str) -> None: ...


class BotHandlers:
    def __init__(self, queue: TaskQueue, dashboard: DashboardLike) -> None:
        self._queue = queue
        self._dashboard = dashboard

    async def handle(self, text: str) -> Reply:
        try:
            name, args = split_command(text)
            if name == "/dl":
                return Reply(self._dl(args), move_dashboard=True)
            if name == "/tasks":
                return Reply(None, move_dashboard=True)
            if name == "/cancel":
                return Reply(self._cancel(parse_cancel(args)), move_dashboard=True)
            if name in ("/help", "/start"):
                return Reply(HELP_TEXT)
            return Reply("未知命令，发送 /help 查看用法")
        except CommandError as exc:
            return Reply(f"❌ {exc}\n\n发送 /help 查看用法")

    def handle_button(self, data: str) -> str:
        """处理看板按钮（视图切换除外），返回弹出提示文字。"""
        if data == BUTTON_REFRESH:
            return REFRESHED_ANSWER
        if data == BUTTON_CANCEL:
            current = self._queue.current()
            if current is None:
                return NO_CURRENT_TASK_ANSWER
            return self._cancel(current.task_id)
        return UNKNOWN_BUTTON_ANSWER

    def register(self, bot_client: Any, owner_id: int) -> None:
        # chats=owner_id 限定私聊：OWNER 在群组里发的命令不会触发
        message_builder = events.NewMessage(from_users=owner_id, chats=owner_id, incoming=True, pattern=COMMAND_PATTERN)
        callback_builder = events.CallbackQuery(chats=owner_id)

        @bot_client.on(message_builder)
        async def _on_command(event: Any) -> None:
            log.info("收到命令: %s", event.raw_text)
            try:
                reply = await self.handle(event.raw_text)
            except Exception:  # 处理器内部错误不能让 Bot 静默，也不能让异常逃到 Telethon 事件循环
                log.exception("处理命令失败: %s", event.raw_text)
                reply = Reply(INTERNAL_ERROR_REPLY)
            if reply.text is not None:
                await event.reply(reply.text, parse_mode=None)
            if reply.move_dashboard:
                await self._dashboard.show()

        @bot_client.on(callback_builder)
        async def _on_button(event: Any) -> None:
            if event.sender_id != owner_id:
                return
            data = bytes(event.data or b"").decode(errors="ignore")
            log.info("收到按钮: %s", data)
            if data.startswith(VIEW_PREFIX):
                await self._dashboard.set_view(data[len(VIEW_PREFIX) :])
                await event.answer()
                return
            answer = self.handle_button(data)
            await self._dashboard.refresh(force=True)
            await event.answer(answer)

    def _dl(self, args: list[str]) -> str:
        state = self._queue.submit(parse_dl(args))
        ahead = len(self._queue.active()) - 1
        return f"✅ 已加入队列，任务 #{state.task_id}，前面还有 {ahead} 个任务\n{state.spec.raw_link}"

    def _cancel(self, task_id: int) -> str:
        if self._queue.cancel(task_id):
            return f"🚫 任务 #{task_id} 已取消"
        return f"任务 #{task_id} 不存在或已结束"
