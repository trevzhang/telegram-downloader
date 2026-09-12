from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest

from tgdl.bot.commands import HELP_TEXT
from tgdl.bot.handlers import BotHandlers
from tgdl.models import ChannelRef, FileResult, FileStatus, MediaItem, MediaKind, TaskSpec, TaskStatus
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


def _item(message_id: int = 1) -> MediaItem:
    return MediaItem(message_id=message_id, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     kind=MediaKind.VIDEO, file_name=f"{message_id}.mp4", size=10)


class _RunningQueue(TaskQueue):
    """让 current() 返回任务 #1，模拟队列正在执行该任务。"""

    def current(self):
        return self.get(1)


async def test_dl_reports_tasks_ahead() -> None:
    handlers, _ = _handlers()
    first = await handlers.handle_text("/dl https://t.me/chana")
    second = await handlers.handle_text("/dl https://t.me/chanb")
    assert "前面还有 0 个任务" in first
    assert "前面还有 1 个任务" in second


async def test_tasks_uses_live_snapshot_for_current_task() -> None:
    tracker = ProgressTracker(task_id=1, channel_title="@c", items=(_item(1), _item(2), _item(3)))
    tracker.on_file_done(FileResult(item=_item(1), path=Path("x"), status=FileStatus.DONE))
    queue = _RunningQueue(runner=None)  # type: ignore[arg-type]
    handlers = BotHandlers(queue, lambda: tracker.snapshot)
    queue.submit(TaskSpec(link=ChannelRef(username="c"), raw_link="https://t.me/c"))
    queue.submit(TaskSpec(link=ChannelRef(username="d"), raw_link="https://t.me/d"))
    reply = await handlers.handle_text("/tasks")
    line_current, line_queued = reply.splitlines()[1:]
    assert line_current.endswith("1/3")
    assert line_queued.endswith("https://t.me/d")


async def test_tasks_falls_back_to_item_count_without_snapshot() -> None:
    handlers, queue = _handlers()
    state = queue.submit(TaskSpec(link=ChannelRef(username="c"), raw_link="https://t.me/c"))
    queue._set(replace(state, status=TaskStatus.DOWNLOADING, items=(_item(1), _item(2))))  # noqa: SLF001
    reply = await handlers.handle_text("/tasks")
    assert "共 2 个" in reply and "/2" not in reply


async def test_status_reports_scanning_task() -> None:
    queue = _RunningQueue(runner=None)  # type: ignore[arg-type]
    handlers = BotHandlers(queue, lambda: None)
    queue.submit(TaskSpec(link=ChannelRef(username="c"), raw_link="https://t.me/c"))
    reply = await handlers.handle_text("/status")
    assert "任务 #1" in reply and "正在扫描" in reply and "https://t.me/c" in reply


class _FakeBotClient:
    def __init__(self) -> None:
        self.builders: list[Any] = []
        self.handlers: list[Any] = []

    def on(self, builder: Any) -> Callable[[Any], Any]:
        self.builders.append(builder)

        def decorator(fn: Any) -> Any:
            self.handlers.append(fn)
            return fn

        return decorator


class _FakeEvent:
    def __init__(self, raw_text: str) -> None:
        self.raw_text = raw_text
        self.replies: list[tuple[str, object]] = []

    async def reply(self, text: str, parse_mode: object = "unset") -> None:
        self.replies.append((text, parse_mode))


async def test_register_restricts_to_owner_private_chat_and_disables_markdown() -> None:
    handlers, _ = _handlers()
    client = _FakeBotClient()
    handlers.register(client, owner_id=42)
    (builder,), (handler,) = client.builders, client.handlers
    assert builder.chats == 42 and builder.from_users == 42 and builder.incoming is True
    event = _FakeEvent("/help")
    await handler(event)
    assert event.replies == [(HELP_TEXT, None)]


async def test_register_handler_reports_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers, _ = _handlers()
    client = _FakeBotClient()
    handlers.register(client, owner_id=42)

    async def boom(text: str) -> str:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(handlers, "handle_text", boom)
    event = _FakeEvent("/tasks")
    await client.handlers[0](event)
    assert len(event.replies) == 1
    text, parse_mode = event.replies[0]
    assert "内部错误" in text and parse_mode is None
