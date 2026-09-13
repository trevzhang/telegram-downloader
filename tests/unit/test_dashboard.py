import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from telethon.errors import MessageNotModifiedError

from tests.fakes.telegram import FakeNotifier
from tgdl.bot.dashboard import (
    ACTION_ROW_IDLE,
    ACTION_ROW_RUNNING,
    VIEW_HISTORY,
    VIEW_PROGRESS,
    VIEW_QUEUE,
    Dashboard,
    DashboardData,
    render_dashboard,
    view_row,
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


def _running_data() -> DashboardData:
    tracker = ProgressTracker(task_id=2, channel_title="@c2", items=(_item(1), _item(2)))
    tracker.on_file_done(FileResult(item=_item(1), path=Path("x"), status=FileStatus.DONE))
    last = replace(_state(1, TaskStatus.DONE, "@c1"), results=(FileResult(_item(), Path("x"), FileStatus.DONE),))
    current = _state(2, TaskStatus.DOWNLOADING, "@c2")
    return DashboardData(
        current=current,
        active=(current, _state(3, TaskStatus.QUEUED), _state(4, TaskStatus.QUEUED)),
        snapshot=tracker.snapshot,
        note="⏳ 限流等待 12 秒",
        finished=(last,),
    )


def test_render_idle_progress_view() -> None:
    view = render_dashboard(DashboardData(), VIEW_PROGRESS)
    assert "没有任务" in view.text
    assert view.buttons == (view_row(VIEW_PROGRESS), ACTION_ROW_IDLE)


def test_render_progress_view_marks_active_tab_and_shows_cancel() -> None:
    view = render_dashboard(_running_data(), VIEW_PROGRESS)
    assert "任务 #2" in view.text and "1/2 个文件" in view.text and "限流等待 12 秒" in view.text
    assert "#3" not in view.text  # 排队详情在队列视图
    assert view.buttons == (view_row(VIEW_PROGRESS), ACTION_ROW_RUNNING)
    assert view.buttons[0][0][0].startswith("•")  # 当前视图标记


def test_render_queue_view_lists_current_and_queued() -> None:
    view = render_dashboard(_running_data(), VIEW_QUEUE)
    assert "#2" in view.text and "1/2" in view.text and "#3" in view.text and "#4" in view.text
    assert view.buttons[0] == view_row(VIEW_QUEUE)


def test_render_history_view_lists_finished() -> None:
    view = render_dashboard(_running_data(), VIEW_HISTORY)
    assert "任务 #1" in view.text and "成功：1" in view.text
    assert "没有" in render_dashboard(DashboardData(), VIEW_HISTORY).text


def test_render_scanning_task_without_snapshot() -> None:
    data = DashboardData(current=_state(5, TaskStatus.SCANNING), active=(_state(5, TaskStatus.SCANNING),))
    assert "任务 #5" in render_dashboard(data, VIEW_PROGRESS).text


class _Dash:
    def __init__(self, notifier: FakeNotifier | None = None, interval: float = 0.01) -> None:
        self.notifier = notifier or FakeNotifier()
        self.data = DashboardData()
        self.dash = Dashboard(self.notifier, lambda: self.data, interval=interval)  # type: ignore[arg-type]


async def test_refresh_sends_once_then_edits_in_place_only_on_change() -> None:
    d = _Dash()
    await d.dash.refresh()
    assert len(d.notifier.sent) == 1 and d.dash.message_id == 1
    await d.dash.refresh()
    assert d.notifier.edits == [] and d.notifier.sent == d.notifier.sent[:1]
    d.data = _running_data()
    await d.dash.refresh()
    assert len(d.notifier.edits) == 1 and d.notifier.edits[0][0] == 1 and d.notifier.deleted == []


async def test_set_view_switches_content_in_place() -> None:
    d = _Dash()
    d.data = _running_data()
    await d.dash.refresh()
    await d.dash.set_view(VIEW_HISTORY)
    assert d.dash.view == VIEW_HISTORY
    assert "任务 #1" in d.notifier.edits[-1][1] and len(d.notifier.sent) == 1
    await d.dash.set_view("bogus")
    assert d.dash.view == VIEW_HISTORY


async def test_force_refresh_and_not_modified_are_fine() -> None:
    class _NotModified(FakeNotifier):
        async def edit(self, message_id: int, text: str, buttons: object = None) -> None:
            raise MessageNotModifiedError(request=None)

    d = _Dash(_NotModified())
    await d.dash.refresh()
    await d.dash.refresh(force=True)
    assert len(d.notifier.sent) == 1 and d.notifier.deleted == []


async def test_edit_failure_resends_once() -> None:
    class _BrokenEdit(FakeNotifier):
        async def edit(self, message_id: int, text: str, buttons: object = None) -> None:
            raise ConnectionError("gone")

    d = _Dash(_BrokenEdit())
    await d.dash.refresh()
    d.data = _running_data()
    await d.dash.refresh()
    assert len(d.notifier.sent) == 2 and d.notifier.deleted == [1] and d.dash.message_id == 2


async def test_concurrent_refreshes_never_produce_two_dashboards() -> None:
    class _Slow(FakeNotifier):
        async def send(self, text: str, buttons: object = None) -> int:
            await asyncio.sleep(0.01)
            return await super().send(text, buttons)

    d = _Dash(_Slow())
    await asyncio.gather(d.dash.refresh(), d.dash.refresh(force=True), d.dash.refresh())
    assert len(d.notifier.sent) == 1


async def test_send_failure_is_tolerated(caplog: pytest.LogCaptureFixture) -> None:
    class _BrokenSend(FakeNotifier):
        async def send(self, text: str, buttons: object = None) -> int:
            raise ConnectionError("no network")

    d = _Dash(_BrokenSend())
    with caplog.at_level("WARNING"):
        await d.dash.refresh()
    assert d.dash.message_id is None and "no network" in caplog.text


async def test_run_loop_wakes_immediately_on_request() -> None:
    d = _Dash(interval=10)
    await d.dash.refresh()
    task = asyncio.create_task(d.dash.run())
    await asyncio.sleep(0)
    d.data = _running_data()
    d.dash.request_refresh()
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(d.notifier.edits) == 1
