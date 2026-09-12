"""装配所有组件并启动：用户客户端登录、Bot 客户端登录、队列循环。"""
from __future__ import annotations

import asyncio
import logging
import sys

from telethon import TelegramClient

from tgdl.bot.handlers import BotHandlers
from tgdl.bot.notifier import TelegramNotifier
from tgdl.config import ConfigError, Settings, load_settings
from tgdl.logging_setup import setup_logging
from tgdl.paths import cleanup_parts
from tgdl.task_queue import TaskQueue
from tgdl.worker import TaskWorker, WorkerConfig

log = logging.getLogger(__name__)


def session_paths(settings: Settings) -> tuple[str, str]:
    return str(settings.data_dir / "user"), str(settings.data_dir / "bot")


def build_worker_config(settings: Settings) -> WorkerConfig:
    return WorkerConfig(
        download_dir=settings.download_dir, concurrency=settings.concurrency,
        max_retries=settings.max_retries, progress_interval=settings.progress_interval,
    )


def build_clients(settings: Settings) -> tuple[TelegramClient, TelegramClient]:
    user_session, bot_session = session_paths(settings)
    proxy = settings.proxy()
    api_hash = settings.api_hash.get_secret_value()
    user = TelegramClient(user_session, settings.api_id, api_hash, proxy=proxy,
                          flood_sleep_threshold=0)  # 让 FloodWait 直接抛出以便显示限流提示
    bot = TelegramClient(bot_session, settings.api_id, api_hash, proxy=proxy)
    return user, bot


async def main_async(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(settings.data_dir / "logs")
    removed = cleanup_parts(settings.download_dir)
    if removed:
        log.info("清理残留 .part 文件 %d 个", removed)

    user, bot = build_clients(settings)
    await user.start()  # 首次运行会在终端交互输入手机号与验证码
    await bot.start(bot_token=settings.bot_token.get_secret_value())
    log.info("用户与 Bot 客户端已登录，代理: %s", "已启用" if settings.proxy() else "直连")

    notifier = TelegramNotifier(bot, settings.owner_id)
    worker = TaskWorker(user, notifier, build_worker_config(settings))
    queue = TaskQueue(worker.run)
    BotHandlers(queue, worker.current_snapshot).register(bot, settings.owner_id)
    await notifier.send("✅ tgdl 已启动，发送 /help 查看用法")
    try:
        await asyncio.gather(queue.run_forever(), bot.run_until_disconnected())
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
    except KeyboardInterrupt:
        print("已退出")


if __name__ == "__main__":
    run()
