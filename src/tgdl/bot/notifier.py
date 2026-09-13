"""通过 Bot 客户端向 OWNER 发送、编辑、删除消息；按钮以 (文字, 回调数据) 元组描述，不暴露 Telethon 类型。"""

from __future__ import annotations

from typing import Any, Protocol

from telethon import Button

Buttons = tuple[tuple[str, str], ...]  # ((按钮文字, 回调数据), ...)，渲染为一行内联按钮


class Notifier(Protocol):
    async def send(self, text: str, buttons: Buttons | None = None) -> int: ...
    async def edit(self, message_id: int, text: str, buttons: Buttons | None = None) -> None: ...
    async def delete(self, message_id: int) -> None: ...


def _inline_row(buttons: Buttons | None) -> list[list[Any]] | None:
    if not buttons:
        return None
    return [[Button.inline(label, data.encode()) for label, data in buttons]]


class TelegramNotifier:
    def __init__(self, bot_client: Any, owner_id: int) -> None:
        self._bot = bot_client
        self._owner_id = owner_id

    async def send(self, text: str, buttons: Buttons | None = None) -> int:
        message = await self._bot.send_message(self._owner_id, text, parse_mode=None, buttons=_inline_row(buttons))
        return int(message.id)

    async def edit(self, message_id: int, text: str, buttons: Buttons | None = None) -> None:
        # 编辑时不传 buttons 会移除原有按钮，调用方需要保留按钮时必须再次传入
        await self._bot.edit_message(self._owner_id, message_id, text, parse_mode=None, buttons=_inline_row(buttons))

    async def delete(self, message_id: int) -> None:
        await self._bot.delete_messages(self._owner_id, message_id)
