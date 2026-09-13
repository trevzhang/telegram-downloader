from collections.abc import Callable
from typing import Any

import pytest

from tgdl.bot.commands import HELP_TEXT
from tgdl.bot.handlers import BotHandlers, DashboardAction, Reply
from tgdl.models import ChannelRef, TaskSpec, TaskStatus
from tgdl.task_queue import TaskQueue


class _FakeDashboard:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def show(self) -> None:
        self.calls.append("show")

    async def refresh(self, force: bool = False) -> None:
        self.calls.append("refresh-force" if force else "refresh")


class _RunningQueue(TaskQueue):
    """让 current() 返回任务 #1，模拟队列正在执行该任务。"""

    def current(self):
        return self.get(1)


def _handlers(queue: TaskQueue | None = None) -> tuple[BotHandlers, TaskQueue, _FakeDashboard]:
    queue = queue or TaskQueue(runner=None)  # type: ignore[arg-type]
    dashboard = _FakeDashboard()
    return BotHandlers(queue, dashboard), queue, dashboard  # type: ignore[arg-type]


def _submit(queue: TaskQueue, name: str = "chana") -> None:
    queue.submit(TaskSpec(link=ChannelRef(username=name), raw_link=f"https://t.me/{name}"))


async def test_dl_submits_task_and_shows_dashboard() -> None:
    handlers, queue, _ = _handlers()
    reply = await handlers.handle("/dl https://t.me/chana --regex 4k")
    assert reply.text is not None and "任务 #1" in reply.text and "已加入队列" in reply.text
    assert reply.dashboard is DashboardAction.SHOW
    assert queue.get(1) is not None


async def test_dl_reports_tasks_ahead() -> None:
    handlers, _, _ = _handlers()
    first = await handlers.handle("/dl https://t.me/chana")
    second = await handlers.handle("/dl https://t.me/chanb")
    assert "前面还有 0 个任务" in (first.text or "") and "前面还有 1 个任务" in (second.text or "")


async def test_dl_error_returns_usage_hint_without_dashboard() -> None:
    handlers, _, _ = _handlers()
    reply = await handlers.handle("/dl nope")
    assert (reply.text or "").startswith("❌") and "/help" in (reply.text or "")
    assert reply.dashboard is DashboardAction.NONE


async def test_status_and_tasks_only_move_dashboard() -> None:
    handlers, _, _ = _handlers()
    for command in ("/status", "/tasks"):
        reply = await handlers.handle(command)
        assert reply == Reply(None, DashboardAction.SHOW)


async def test_cancel_reports_result_and_refreshes_dashboard() -> None:
    handlers, queue, _ = _handlers()
    missing = await handlers.handle("/cancel 9")
    assert "不存在" in (missing.text or "") and missing.dashboard is DashboardAction.REFRESH
    await handlers.handle("/dl https://t.me/chana")
    done = await handlers.handle("/cancel 1")
    assert "已取消" in (done.text or "") and queue.get(1).status is TaskStatus.CANCELLED


async def test_help_and_unknown() -> None:
    handlers, _, _ = _handlers()
    assert (await handlers.handle("/help")).text == HELP_TEXT
    assert (await handlers.handle("/start")).text == HELP_TEXT
    assert "未知命令" in ((await handlers.handle("/wat")).text or "")


def test_button_refresh_and_cancel_current() -> None:
    handlers, queue, _ = _handlers(_RunningQueue(runner=None))  # type: ignore[arg-type]
    assert "没有正在执行" in handlers.handle_button("cancel")
    _submit(queue)
    assert handlers.handle_button("refresh") == "已刷新"
    assert "任务 #1 已取消" in handlers.handle_button("cancel")
    assert "未知" in handlers.handle_button("nope")


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
    def __init__(self, raw_text: str = "", data: bytes = b"", sender_id: int = 42) -> None:
        self.raw_text, self.data, self.sender_id = raw_text, data, sender_id
        self.replies: list[tuple[str, object]] = []
        self.answers: list[str] = []

    async def reply(self, text: str, parse_mode: object = "unset") -> Any:
        self.replies.append((text, parse_mode))
        return type("Sent", (), {"id": 77})()

    async def answer(self, text: str = "") -> None:
        self.answers.append(text)


def _registered() -> tuple[BotHandlers, _FakeBotClient, _FakeDashboard]:
    handlers, _, dashboard = _handlers()
    client = _FakeBotClient()
    handlers.register(client, owner_id=42)
    return handlers, client, dashboard


async def test_register_builders_restrict_to_owner_private_chat() -> None:
    _, client, _ = _registered()
    message_builder, callback_builder = client.builders
    assert message_builder.chats == 42 and message_builder.from_users == 42 and message_builder.incoming is True
    assert callback_builder.chats == 42


async def test_command_handler_replies_without_markdown_and_drives_dashboard() -> None:
    _, client, dashboard = _registered()
    on_command = client.handlers[0]
    event = _FakeEvent("/dl https://t.me/chana")
    await on_command(event)
    assert event.replies[0][1] is None and "任务 #1" in event.replies[0][0]
    assert dashboard.calls == ["show"]
    status = _FakeEvent("/status")
    await on_command(status)
    assert status.replies == [] and dashboard.calls == ["show", "show"]
    await on_command(_FakeEvent("/help"))
    assert dashboard.calls == ["show", "show"]


async def test_command_handler_reports_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers, client, _ = _registered()

    async def boom(text: str) -> Reply:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(handlers, "handle", boom)
    event = _FakeEvent("/tasks")
    await client.handlers[0](event)
    text, parse_mode = event.replies[0]
    assert "内部错误" in text and parse_mode is None


async def test_button_handler_answers_and_force_refreshes() -> None:
    _, client, dashboard = _registered()
    on_button = client.handlers[1]
    event = _FakeEvent(data=b"refresh")
    await on_button(event)
    assert event.answers == ["已刷新"] and dashboard.calls == ["refresh-force"]
    stranger = _FakeEvent(data=b"refresh", sender_id=7)
    await on_button(stranger)
    assert stranger.answers == [] and dashboard.calls == ["refresh-force"]
