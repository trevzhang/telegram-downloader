from dataclasses import replace
from datetime import datetime, timezone

from tgdl.bot.handlers import BotHandlers
from tgdl.models import ChannelRef, MediaItem, MediaKind, TaskSpec, TaskStatus
from tgdl.progress import ProgressTracker
from tgdl.task_queue import TaskQueue


def _handlers(snapshot=None) -> tuple[BotHandlers, TaskQueue]:
    queue = TaskQueue(runner=None)  # type: ignore[arg-type]
    return BotHandlers(queue, lambda: snapshot), queue


async def test_dl_submits_task() -> None:
    handlers, queue = _handlers()
    reply = await handlers.handle_text("/dl https://t.me/chan --regex 4k")
    assert "任务 #1" in reply and "已加入队列" in reply
    assert queue.get(1) is not None


async def test_dl_error_returns_usage_hint() -> None:
    handlers, _ = _handlers()
    reply = await handlers.handle_text("/dl nope")
    assert reply.startswith("❌") and "/help" in reply


async def test_tasks_empty_and_listed() -> None:
    handlers, queue = _handlers()
    assert "没有任务" in await handlers.handle_text("/tasks")
    queue.submit(TaskSpec(link=ChannelRef(username="c"), raw_link="https://t.me/c"))
    reply = await handlers.handle_text("/tasks")
    assert "#1" in reply and "排队中" in reply


async def test_cancel_reports_result() -> None:
    handlers, queue = _handlers()
    assert "不存在" in await handlers.handle_text("/cancel 9")
    await handlers.handle_text("/dl https://t.me/chan")
    assert "已取消" in await handlers.handle_text("/cancel 1")
    assert queue.get(1).status is TaskStatus.CANCELLED


async def test_status_without_task() -> None:
    handlers, _ = _handlers()
    assert "没有正在下载" in await handlers.handle_text("/status")


async def test_status_renders_snapshot() -> None:
    item = MediaItem(message_id=1, date=datetime(2026, 1, 1, tzinfo=timezone.utc), kind=MediaKind.VIDEO, file_name="a.mp4", size=10)
    tracker = ProgressTracker(task_id=7, channel_title="@c", items=(item,))
    handlers, _ = _handlers(tracker.snapshot)
    assert "任务 #7" in await handlers.handle_text("/status")


async def test_help_and_unknown() -> None:
    handlers, _ = _handlers()
    assert "用法" in await handlers.handle_text("/help")
    assert "用法" in await handlers.handle_text("/start")
    assert "未知命令" in await handlers.handle_text("/wat")
