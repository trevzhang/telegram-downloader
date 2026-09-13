from typing import Any

from tgdl.bot.notifier import TelegramNotifier


class _Sent:
    id = 9


class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def send_message(self, *args: Any, **kwargs: Any) -> _Sent:
        self.calls.append(("send", args, kwargs))
        return _Sent()

    async def edit_message(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("edit", args, kwargs))

    async def delete_messages(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("delete", args, kwargs))


async def test_send_edit_delete_map_buttons_and_disable_markdown() -> None:
    bot = _FakeBot()
    notifier = TelegramNotifier(bot, owner_id=42)
    assert (
        await notifier.send(
            "hi", buttons=((("🔄 刷新", "refresh"), ("🚫 取消", "cancel")), (("📜 历史", "view:history"),))
        )
        == 9
    )
    await notifier.edit(9, "hi2")
    await notifier.delete(9)
    send, edit, delete = bot.calls
    assert send[1] == (42, "hi") and send[2]["parse_mode"] is None
    rows = send[2]["buttons"]
    assert [[b.text for b in row] for row in rows] == [["🔄 刷新", "🚫 取消"], ["📜 历史"]]
    assert type(rows[0][0]).__name__ == "KeyboardInlineButton" and rows[0][1].type.data == b"cancel"
    assert edit[1] == (42, 9, "hi2") and edit[2] == {"parse_mode": None, "buttons": None}
    assert delete[1] == (42, 9)
