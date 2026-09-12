"""装配所有组件并启动：用户客户端登录、Bot 客户端登录、队列循环。"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable
from typing import Any

from telethon import TelegramClient

from tgdl.bot.handlers import BotHandlers
from tgdl.bot.notifier import Notifier, TelegramNotifier
from tgdl.config import ConfigError, Settings, load_settings
from tgdl.logging_setup import LOG_DIR_NAME, setup_logging
from tgdl.paths import cleanup_parts
from tgdl.task_queue import TaskQueue
from tgdl.worker import TaskWorker, WorkerConfig

log = logging.getLogger(__name__)

USER_SESSION_NAME = "user"
BOT_SESSION_NAME = "bot"
STARTUP_MESSAGE = "✅ tgdl 已启动，发送 /help 查看用法"
NON_TTY_LOGIN_MESSAGE = "首次登录需要在交互终端运行（输入手机号和验证码）"
BOT_SESSION_MISMATCH_MESSAGE = "data/bot.session 属于另一个 Bot，请删除后重试"
BOT_TOKEN_FORMAT_MESSAGE = "BOT_TOKEN 格式不正确，应形如 123456:ABC-DEF"
USER_SESSION_IS_BOT_MESSAGE = (
    "data/user.session 登录的是 Bot 而不是你的个人账号，Bot 无法读取频道历史。"
    "请删除 data/user.session 后重新运行，并在提示时输入手机号（不是 Bot Token）"
)
PHONE_PROMPT = "请输入你的个人账号手机号（含国际区号，如 +8613800000000）："
PHONE_LOOKS_LIKE_TOKEN_MESSAGE = "这里需要的是手机号，不是 Bot Token，请重新输入"
BOT_TOKEN_SEPARATOR = ":"


def session_paths(settings: Settings) -> tuple[str, str]:
    return str(settings.data_dir / USER_SESSION_NAME), str(settings.data_dir / BOT_SESSION_NAME)


def build_worker_config(settings: Settings) -> WorkerConfig:
    return WorkerConfig(
        download_dir=settings.download_dir,
        concurrency=settings.concurrency,
        max_retries=settings.max_retries,
        progress_interval=settings.progress_interval,
    )


def build_user_client(settings: Settings) -> TelegramClient:
    """用户客户端关闭 Telethon 自动限流等待，让 FloodWait 直接抛出以便显示限流提示。"""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    user_session, _ = session_paths(settings)
    return TelegramClient(
        user_session,
        settings.api_id,
        settings.api_hash.get_secret_value(),
        proxy=settings.proxy(),
        flood_sleep_threshold=0,
    )


def build_bot_client(settings: Settings) -> TelegramClient:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _, bot_session = session_paths(settings)
    return TelegramClient(bot_session, settings.api_id, settings.api_hash.get_secret_value(), proxy=settings.proxy())


def build_clients(settings: Settings) -> tuple[TelegramClient, TelegramClient]:
    return build_user_client(settings), build_bot_client(settings)


def bot_id_from_token(token: str) -> int:
    """Bot Token 形如 `<bot_id>:<secret>`，冒号前的整数即 Bot 的用户 ID。"""
    head, sep, _ = token.partition(BOT_TOKEN_SEPARATOR)
    if not sep or not head.isdigit():
        raise ConfigError(BOT_TOKEN_FORMAT_MESSAGE)
    return int(head)


def ask_phone() -> str:
    """首次登录时询问手机号；Telethon 默认提示允许输入 Bot Token，这里明确拒绝。"""
    while True:
        value = input(PHONE_PROMPT).strip()
        if BOT_TOKEN_SEPARATOR in value:
            print(PHONE_LOOKS_LIKE_TOKEN_MESSAGE)
            continue
        return value


async def start_clients(user: TelegramClient, bot: TelegramClient, settings: Settings) -> None:
    """登录两个客户端；首次登录必须在交互终端，且 bot.session 必须与 BOT_TOKEN 属于同一个 Bot。"""
    await user.connect()
    if not await user.is_user_authorized() and not sys.stdin.isatty():
        raise ConfigError(NON_TTY_LOGIN_MESSAGE)
    await user.start(phone=ask_phone)  # 首次运行会在终端交互输入手机号与验证码
    if (await user.get_me()).bot:
        raise ConfigError(USER_SESSION_IS_BOT_MESSAGE)
    token = settings.bot_token.get_secret_value()
    await bot.start(bot_token=token)  # 已有 session 时 Telethon 不会用 token 重新登录
    me = await bot.get_me()
    if me.id != bot_id_from_token(token):
        raise ConfigError(BOT_SESSION_MISMATCH_MESSAGE)


async def _send_startup_notice(notifier: Notifier) -> None:
    try:
        await notifier.send(STARTUP_MESSAGE)
    except Exception as exc:  # 通知失败不影响启动，常见原因是 OWNER 还没给 Bot 发过 /start
        log.warning("启动通知发送失败（请先在 Telegram 打开 Bot 并发送 /start）：%s", exc)


async def _cancel(task: asyncio.Task[Any]) -> None:
    """取消并等待任务结束；关停阶段的次要异常只记录，不掩盖主异常。"""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log.warning("关停任务时出现异常，已忽略：%s", exc)


async def _run_until_first_done(queue_run: Awaitable[None], bot_run: Awaitable[None]) -> None:
    """任一循环结束即关停另一个：队列异常会重新抛出，Bot 断开则正常返回。"""
    queue_task = asyncio.ensure_future(queue_run)
    bot_task = asyncio.ensure_future(bot_run)
    try:
        done, _ = await asyncio.wait({queue_task, bot_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (queue_task, bot_task):
            if not task.done():
                await _cancel(task)
    for task in done:
        task.result()  # 重新抛出已结束任务的异常
    if bot_task in done:
        log.info("Bot 连接已断开，正在关停")


async def main_async(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(settings.data_dir / LOG_DIR_NAME)
    removed = cleanup_parts(settings.download_dir)
    if removed:
        log.info("清理残留 .part 文件 %d 个", removed)

    user, bot = build_clients(settings)
    try:
        await start_clients(user, bot, settings)
        log.info("用户与 Bot 客户端已登录，代理: %s", "已启用" if settings.proxy() else "直连")
        notifier = TelegramNotifier(bot, settings.owner_id)
        worker = TaskWorker(user, notifier, build_worker_config(settings))
        queue = TaskQueue(worker.run)
        BotHandlers(queue, worker.current_snapshot).register(bot, settings.owner_id)
        await _send_startup_notice(notifier)
        await _run_until_first_done(queue.run_forever(), bot.run_until_disconnected())
    finally:
        await user.disconnect()
        await bot.disconnect()


def run() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"启动失败: {exc}\n请参考 .env.example 创建 .env", file=sys.stderr)
        sys.exit(1)
    try:
        asyncio.run(main_async(settings))
    except ConfigError as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("已退出")


if __name__ == "__main__":
    run()
