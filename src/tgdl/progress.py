"""进度聚合：滑动窗口速度、ETA、文本渲染。快照不可变，Tracker 只替换引用。"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable

from tgdl.models import FileResult, FileStatus, MediaItem, TaskState, TaskStatus

BAR_WIDTH = 15
SPEED_WINDOW_SECONDS = 10.0
MAX_ACTIVE_LINES = 5
MAX_FAILED_LINES = 10

STATUS_LABEL = {
    TaskStatus.QUEUED: "排队中",
    TaskStatus.SCANNING: "扫描中",
    TaskStatus.DOWNLOADING: "下载中",
    TaskStatus.DONE: "已完成",
    TaskStatus.FAILED: "失败",
    TaskStatus.CANCELLED: "已取消",
}


@dataclass(frozen=True)
class SpeedWindow:
    span: float = SPEED_WINDOW_SECONDS
    samples: tuple[tuple[float, int], ...] = ()

    def add(self, now: float, total_bytes: int) -> SpeedWindow:
        kept = tuple(s for s in self.samples if now - s[0] <= self.span)
        return replace(self, samples=kept + ((now, total_bytes),))

    def speed(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self.samples[0], self.samples[-1]
        return (b1 - b0) / (t1 - t0) if t1 > t0 else 0.0


def eta_seconds(remaining_bytes: int, speed: float) -> float | None:
    if speed <= 0 or remaining_bytes <= 0:
        return None
    return remaining_bytes / speed


def format_bytes(n: float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "未知"
    total = int(seconds)
    if total < 60:
        return f"{total} 秒"
    if total < 3600:
        return f"约 {total // 60} 分钟"
    return f"约 {total // 3600} 小时 {total % 3600 // 60} 分钟"


def render_bar(fraction: float, width: int = BAR_WIDTH) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "▓" * filled + "░" * (width - filled)


@dataclass(frozen=True)
class FileProgress:
    message_id: int
    name: str
    current: int
    total: int


@dataclass(frozen=True)
class ProgressSnapshot:
    task_id: int
    channel_title: str
    status: TaskStatus
    total_files: int
    total_bytes: int
    done: int = 0
    skipped: int = 0
    failed: int = 0
    finished_bytes: int = 0
    active: tuple[FileProgress, ...] = ()
    speed: float = 0.0
    flood_wait: int | None = None

    @property
    def done_bytes(self) -> int:
        return self.finished_bytes + sum(f.current for f in self.active)

    @property
    def finished_files(self) -> int:
        return self.done + self.skipped + self.failed

    @property
    def fraction(self) -> float:
        return self.done_bytes / self.total_bytes if self.total_bytes else 0.0


_COUNTER_FIELD = {FileStatus.DONE: "done", FileStatus.SKIPPED: "skipped", FileStatus.FAILED: "failed"}


class ProgressTracker:
    """持有最新的不可变快照；每次更新生成新快照替换引用。"""

    def __init__(self, task_id: int, channel_title: str, items: tuple[MediaItem, ...],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._window = SpeedWindow()
        self._snap = ProgressSnapshot(
            task_id=task_id, channel_title=channel_title, status=TaskStatus.DOWNLOADING,
            total_files=len(items), total_bytes=sum(i.size for i in items),
        )

    @property
    def snapshot(self) -> ProgressSnapshot:
        return self._snap

    def on_file_progress(self, message_id: int, name: str, current: int, total: int) -> None:
        others = tuple(f for f in self._snap.active if f.message_id != message_id)
        entry = FileProgress(message_id=message_id, name=name, current=current, total=total)
        self._snap = replace(self._snap, active=others + (entry,), flood_wait=None)
        self._tick()

    def on_file_done(self, result: FileResult) -> None:
        others = tuple(f for f in self._snap.active if f.message_id != result.item.message_id)
        field = _COUNTER_FIELD[result.status]
        gained = result.item.size if result.status is not FileStatus.FAILED else 0
        self._snap = replace(
            self._snap, active=others, finished_bytes=self._snap.finished_bytes + gained,
            **{field: getattr(self._snap, field) + 1},
        )
        self._tick()

    def on_flood_wait(self, seconds: int) -> None:
        self._snap = replace(self._snap, flood_wait=seconds)

    def _tick(self) -> None:
        self._window = self._window.add(self._clock(), self._snap.done_bytes)
        self._snap = replace(self._snap, speed=self._window.speed())


def render_progress(snap: ProgressSnapshot) -> str:
    remaining = snap.total_bytes - snap.done_bytes
    lines = [
        f"📥 任务 #{snap.task_id}  {snap.channel_title}",
        f"状态：{STATUS_LABEL[snap.status]}  {snap.finished_files}/{snap.total_files} 个文件",
        f"进度：{render_bar(snap.fraction)} {snap.fraction * 100:.1f}%  "
        f"({format_bytes(snap.done_bytes)} / {format_bytes(snap.total_bytes)})",
        f"速度：{format_bytes(snap.speed)}/s    剩余：{format_duration(eta_seconds(remaining, snap.speed))}",
    ]
    if snap.flood_wait:
        lines.append(f"⚠️ 限流等待 {snap.flood_wait} 秒")
    if snap.active:
        lines.append("正在下载：")
        for entry in snap.active[:MAX_ACTIVE_LINES]:
            pct = entry.current / entry.total * 100 if entry.total else 0.0
            lines.append(f"  • {entry.name}  {pct:.0f}%")
    lines.append(f"已跳过：{snap.skipped} 个（已存在）  失败：{snap.failed} 个")
    return "\n".join(lines)


def render_summary(state: TaskState) -> str:
    counts = {status: sum(1 for r in state.results if r.status is status) for status in FileStatus}
    total_bytes = sum(r.item.size for r in state.results if r.status is not FileStatus.FAILED)
    icon = {TaskStatus.DONE: "✅", TaskStatus.CANCELLED: "🚫", TaskStatus.FAILED: "❌"}.get(state.status, "ℹ️")
    lines = [
        f"{icon} 任务 #{state.task_id}  {state.channel_title}  {STATUS_LABEL[state.status]}",
        f"成功：{counts[FileStatus.DONE]}  跳过：{counts[FileStatus.SKIPPED]}  失败：{counts[FileStatus.FAILED]}"
        f"  共 {len(state.items)} 个，{format_bytes(total_bytes)}",
    ]
    failures = tuple(r for r in state.results if r.status is FileStatus.FAILED)
    if failures:
        lines.append("失败列表：")
        lines.extend(f"  • {r.item.file_name}: {r.error}" for r in failures[:MAX_FAILED_LINES])
        if len(failures) > MAX_FAILED_LINES:
            lines.append(f"  …另有 {len(failures) - MAX_FAILED_LINES} 个，详见日志")
    if state.error:
        lines.append(f"错误：{state.error}")
    return "\n".join(lines)
