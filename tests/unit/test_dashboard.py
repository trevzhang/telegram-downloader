import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fakes.telegram import FakeNotifier
from tgdl.bot.dashboard import (
    CANCEL_BUTTON,
    REFRESH_BUTTON,
    Dashboard,
    DashboardData,
    DashboardView,
    render_dashboard,
)
from tgdl.models import ChannelRef, FileResult, FileStatus, MediaItem, MediaKind, TaskSpec, TaskState, TaskStatus
from tgdl.progress import ProgressTracker


def _state(task_id: int, status: TaskStatus, title: str = "") -> TaskState:
    spec = TaskSpec(link=ChannelRef(username=f"chan{task_id}"), raw_link=f"https://t.me/chan{task_id}")
    return TaskState(task_id=task_id, spec=spec, status=status, channel_title=title)


def _item(message_id: int = 1) -> MediaItem:
    return MediaItem(
        message_id=message_id, date=datetime(2026, 1, 1, tzinfo=UTC), kind=MediaKind.VIDEO, file_name="v.mp4", size=10
    )


def test_render_idle_dashboard_has_only_refresh_button() -> None:
    view = render_dashboard(DashboardData())
    assert "没有任务" in view.text and view.buttons == (REFRESH_BUTTON,)


def test_render_running_dashboard_shows_progress_queue_note_and_last_result() -> None:
    tracker = ProgressTracker(task_id=2, channel_title="@c2", items=(_item(1), _item(2)))
    tracker.on_file_done(FileResult(item=_item(1), path=Path("x"), status=FileStatus.DONE))
    last = replace(_state(1, TaskStatus.DONE, "@c1"), results=(FileResult(_item(), Path("x"), FileStatus.DONE),))
    data = DashboardData(
        current=_state(2, TaskStatus.DOWNLOADING, "@c2"),
        active=(_state(2, TaskStatus.DOWNLOADING, "@c2"), _state(3, TaskStatus.QUEUED), _state(4, TaskStatus.QUEUED)),
        snapshot=tracker.snapshot,
        note="⏳ 限流等待 12 秒",
        last_finished=last,
    )
    view = render_dashboard(data)
    assert "任务 #2" in view.text and "1/2 个文件" in view.text
    assert "排队中" in view.text and "#3" in view.text and "#4" in view.text and "#2" in view.text
    assert "限流等待 12 秒" in view.text
    assert "上一个" in view.text and "任务 #1" in view.text
    assert view.buttons == (REFRESH_BUTTON, CANCEL_BUTTON)


def test_render_scanning_task_without_snapshot() -> None:
    view = render_dashboard(
        DashboardData(current=_state(5, TaskStatus.SCANNING), active=(_state(5, TaskStatus.SCANNING),))
    )
    assert "任务 #5" in view.text and "扫描" in view.text and "https://t.me/chan5" in view.text


def _dashboard(views: list[DashboardView], notifier: FakeNotifier | None = None) -> tuple[Dashboard, FakeNotifier]:
    notifier = notifier or FakeNotifier()
    it = iter(views)
    last = views[-1]
    return Dashboard(notifier, lambda: next(it, last), interval=0.01), notifier  # type: ignore[arg-type]


async def test_show_sends_once_then_replaces_previous_message() -> None:
    dash, notifier = _dashboard([DashboardView("a", (REFRESH_BUTTON,))])
    await dash.show()
    assert notifier.sent == ["a"] and notifier.buttons[1] == (REFRESH_BUTTON,) and dash.message_id == 1
    await dash.show()
    assert notifier.deleted == [1] and notifier.sent == ["a", "a"] and dash.message_id == 2


async def test_refresh_edits_only_on_change_and_keeps_buttons() -> None:
    views = [
        DashboardView("a", (REFRESH_BUTTON,)),
        DashboardView("a", (REFRESH_BUTTON,)),
        DashboardView("b", (REFRESH_BUTTON, CANCEL_BUTTON)),
    ]
    dash, notifier = _dashboard(views)
    await dash.refresh()  # 尚无消息：等同于 show
    assert notifier.sent == ["a"] and notifier.edits == []
    await dash.refresh()
    assert notifier.edits == []
    await dash.refresh()
    assert notifier.edits == [(1, "b")] and notifier.buttons[1] == (REFRESH_BUTTON, CANCEL_BUTTON)


async def test_force_refresh_edits_even_when_unchanged() -> None:
    dash, notifier = _dashboard([DashboardView("a", ())])
    await dash.show()
    await dash.refresh(force=True)
    assert notifier.edits == [(1, "a")]


async def test_edit_failure_resends_dashboard(caplog: pytest.LogCaptureFixture) -> None:
    class _BrokenEdit(FakeNotifier):
        async def edit(self, message_id: int, text: str, buttons: object = None) -> None:
            raise ConnectionError("gone")

    dash, notifier = _dashboard([DashboardView("a", ()), DashboardView("b", ())], _BrokenEdit())
    await dash.show()
    await dash.refresh()
    assert notifier.sent == ["a", "b"] and notifier.deleted == [1] and dash.message_id == 2


async def test_send_failure_is_tolerated(caplog: pytest.LogCaptureFixture) -> None:
    class _BrokenSend(FakeNotifier):
        async def send(self, text: str, buttons: object = None) -> int:
            raise ConnectionError("no network")

    dash, _ = _dashboard([DashboardView("a", ())], _BrokenSend())
    with caplog.at_level("WARNING"):
        await dash.show()
    assert dash.message_id is None and "no network" in caplog.text


async def test_run_loop_wakes_immediately_on_request() -> None:
    views = [DashboardView("a", ()), DashboardView("b", ())]
    dash, notifier = _dashboard(views)
    dash._interval = 10  # noqa: SLF001
    await dash.show()
    task = asyncio.create_task(dash.run())
    await asyncio.sleep(0)
    dash.request_refresh()
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert notifier.edits == [(1, "b")]
