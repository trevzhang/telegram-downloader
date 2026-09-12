"""通过 Bot 客户端向 OWNER 发送与编辑消息。"""
from __future__ import annotations

from typing import Any, Protocol


class Notifier(Protocol):
    async def send(self, text: str) -> int: ...
    async def edit(self, message_id: int, text: str) -> None: ...


class TelegramNotifier:
    def __init__(self, bot_client: Any, owner_id: int) -> None:
        self._bot = bot_client
        self._owner_id = owner_id

    async def send(self, text: str) -> int:
        message = await self._bot.send_message(self._owner_id, text, parse_mode=None)
        return int(message.id)

    async def edit(self, message_id: int, text: str) -> None:
        await self._bot.edit_message(self._owner_id, message_id, text, parse_mode=None)
