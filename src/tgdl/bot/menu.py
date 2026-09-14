"""向 Telegram 注册 Bot 命令菜单，让用户输入 / 时能直接选择命令。"""

from __future__ import annotations

from typing import Any

from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault

BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("download", "下载：/download <链接> [起始ID 结束ID] [过滤表达式]"),
    ("tasks", "显示看板：当前进度、队列与历史"),
    ("cancel", "取消任务：/cancel <任务ID>"),
    ("help", "用法说明"),
)


async def register_commands(bot_client: Any) -> None:
    commands = [BotCommand(command=name, description=desc) for name, desc in BOT_COMMANDS]
    await bot_client(SetBotCommandsRequest(scope=BotCommandScopeDefault(), lang_code="", commands=commands))
