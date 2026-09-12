from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from tgdl.models import ChannelRef, FileResult, FileStatus, MediaItem, MediaKind, TaskSpec, TaskState, TaskStatus
from tgdl.progress import (
    ProgressTracker, SpeedWindow, eta_seconds, format_bytes, format_duration,
    render_bar, render_progress, render_summary,
)


def _item(mid: int, size: int = 100, name: str = "f.mp4") -> MediaItem:
    return MediaItem(message_id=mid, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     kind=MediaKind.VIDEO, file_name=name, size=size)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_speed_window_needs_two_samples() -> None:
    assert SpeedWindow().add(0.0, 0).speed() == 0.0


def test_speed_window_computes_average() -> None:
    window = SpeedWindow().add(0.0, 0).add(2.0, 200)
    assert window.speed() == 100.0


def test_speed_window_drops_old_samples() -> None:
    window = SpeedWindow(span=10.0).add(0.0, 0).add(5.0, 50).add(20.0, 200)
    assert window.samples[0] == (20.0, 200)


def test_eta() -> None:
    assert eta_seconds(1000, 100.0) == 10.0
    assert eta_seconds(1000, 0.0) is None
    assert eta_seconds(0, 5.0) is None


def test_format_bytes() -> None:
    assert format_bytes(512) == "512 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(3 * 1024**3) == "3.0 GB"


def test_format_duration() -> None:
    assert format_duration(None) == "未知"
    assert format_duration(45) == "45 秒"
    assert format_duration(420) == "约 7 分钟"
    assert format_duration(3900) == "约 1 小时 5 分钟"


def test_render_bar() -> None:
    assert render_bar(0.0) == "░" * 15
    assert render_bar(1.0) == "▓" * 15
    assert render_bar(0.5).count("▓") == 8


def test_tracker_progress_and_done() -> None:
    clock = _Clock()
    tracker = ProgressTracker(task_id=1, channel_title="@c", items=(_item(1), _item(2)), clock=clock)
    assert tracker.snapshot.total_bytes == 200

    tracker.on_file_progress(1, "f.mp4", 50, 100)
    clock.now = 1.0
    tracker.on_file_progress(1, "f.mp4", 100, 100)
    assert tracker.snapshot.done_bytes == 100
    assert tracker.snapshot.speed == 50.0

    tracker.on_file_done(FileResult(item=_item(1), path=Path("x"), status=FileStatus.DONE))
    snap = tracker.snapshot
    assert snap.done == 1 and snap.active == ()
    assert snap.finished_bytes == 100

    tracker.on_file_done(FileResult(item=_item(2), path=Path("y"), status=FileStatus.SKIPPED))
    assert tracker.snapshot.skipped == 1
    assert tracker.snapshot.fraction == 1.0


def test_tracker_failed_does_not_count_bytes() -> None:
    tracker = ProgressTracker(task_id=1, channel_title="@c", items=(_item(1),))
    tracker.on_file_done(FileResult(item=_item(1), path=Path("x"), status=FileStatus.FAILED, error="boom"))
    assert tracker.snapshot.failed == 1
    assert tracker.snapshot.finished_bytes == 0


def test_render_progress_contains_key_fields() -> None:
    tracker = ProgressTracker(task_id=3, channel_title="@chan", items=(_item(1, name="a.mp4"),))
    tracker.on_file_progress(1, "a.mp4", 78, 100)
    tracker.on_flood_wait(12)
    text = render_progress(tracker.snapshot)
    assert "任务 #3" in text and "@chan" in text
    assert "0/1 个文件" in text
    assert "a.mp4  78%" in text
    assert "限流等待 12 秒" in text


def test_render_summary_lists_failures() -> None:
    spec = TaskSpec(link=ChannelRef(username="c"), raw_link="x")
    results = (
        FileResult(item=_item(1), path=Path("a"), status=FileStatus.DONE),
        FileResult(item=_item(2, name="bad.mp4"), path=Path("b"), status=FileStatus.FAILED, error="timeout"),
    )
    state = TaskState(task_id=1, spec=spec, status=TaskStatus.DONE, channel_title="@c", items=(_item(1), _item(2)), results=results)
    text = render_summary(state)
    assert "成功：1" in text and "失败：1" in text
    assert "bad.mp4" in text and "timeout" in text
    assert "已取消" in render_summary(replace(state, status=TaskStatus.CANCELLED))
