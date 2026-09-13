from typing import Any

from telethon.tl.functions.bots import SetBotCommandsRequest

from tgdl.bot.menu import BOT_COMMANDS, register_commands


class _FakeBot:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def __call__(self, request: Any) -> bool:
        self.requests.append(request)
        return True


async def test_register_commands_sends_menu_matching_help() -> None:
    bot = _FakeBot()
    await register_commands(bot)
    (request,) = bot.requests
    assert isinstance(request, SetBotCommandsRequest)
    assert [c.command for c in request.commands] == [name for name, _ in BOT_COMMANDS]
    assert {"dl", "status", "tasks", "cancel", "help"} <= {c.command for c in request.commands}
