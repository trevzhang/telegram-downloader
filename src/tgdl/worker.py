"""单个任务的完整执行流程：解析频道 → 扫描 → 并发下载 → 汇总。

进度不再由 worker 发消息，而是通过 current_snapshot()/current_note() 暴露给看板；
worker 只在任务结束时发一条静态汇总。通知只是尽力而为：发送失败只记录警告，绝不改变任务结果。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar

from tgdl.bot.notifier import Notifier
from tgdl.downloader import FLOOD_ERRORS, FLOOD_WAIT_MARGIN_SECONDS, MAX_FLOOD_WAIT_TOTAL_SECONDS, download_all
from tgdl.filters import FilterError, build_filter
from tgdl.models import FileResult, TaskState, TaskStatus
from tgdl.paths import DownloadDirMissingError, channel_dir_name, ensure_download_root
from tgdl.progress import ProgressSnapshot, ProgressTracker, render_summary
from tgdl.scanner import ChannelAccessError, resolve_channel, scan

log = logging.getLogger(__name__)
NOTIFY_TIMEOUT_SECONDS = 15.0

Publish = Callable[[TaskState], None]
SleepFn = Callable[[float], Awaitable[None]]
FloodNotify = Callable[[int], Awaitable[None]]
T = TypeVar("T")

UNKNOWN_ENTITY_ID = "?"
FLOOD_CAP_MESSAGE = "限流等待超过上限，请稍后重试"


@dataclass(frozen=True)
class WorkerConfig:
    download_dir: Path
    concurrency: int
    max_retries: int
    notify_timeout: float = NOTIFY_TIMEOUT_SECONDS  # 单次 Bot 通知的最长等待，防止网络卡死阻塞任务与关停


class _CollectingTracker(ProgressTracker):
    """在进度快照之外保留每个文件的最终结果，供取消或异常时渲染部分汇总。"""

    def __init__(self, task_id: int, channel_title: str, items: tuple) -> None:
        super().__init__(task_id, channel_title, items)
        self._results: tuple[FileResult, ...] = ()

    @property
    def results(self) -> tuple[FileResult, ...]:
        return self._results

    def on_file_done(self, result: FileResult) -> None:
        self._results = self._results + (result,)
        super().on_file_done(result)


async def _with_flood_retry(coro_factory: Callable[[], Awaitable[T]], notify: FloodNotify, sleep: SleepFn) -> T:
    """扫描阶段的限流：通知、等待后重试；累计等待超过上限则抛 ChannelAccessError 让任务明确失败。"""
    waited = 0
    while True:
        try:
            return await coro_factory()
        except FLOOD_ERRORS as exc:
            waited += exc.seconds
            if waited > MAX_FLOOD_WAIT_TOTAL_SECONDS:
                log.error("扫描阶段限流 %d 秒，累计 %d 秒超过上限", exc.seconds, waited)
                raise ChannelAccessError(FLOOD_CAP_MESSAGE) from exc
            log.warning("扫描阶段限流 %d 秒，等待后重试", exc.seconds)
            await notify(exc.seconds)
            await sleep(exc.seconds + FLOOD_WAIT_MARGIN_SECONDS)


def display_title(entity: Any) -> str:
    """看板/汇总里的频道展示名：名称便于区分，@用户名可直达，两者都有时一起显示。"""
    title = getattr(entity, "title", None)
    username = getattr(entity, "username", None)
    if title and username:
        return f"{title} @{username}"
    return title or (f"@{username}" if username else str(getattr(entity, "id", UNKNOWN_ENTITY_ID)))


class TaskWorker:
    def __init__(
        self, user_client: Any, notifier: Notifier, config: WorkerConfig, *, sleep: SleepFn = asyncio.sleep
    ) -> None:
        self._client = user_client
        self._notifier = notifier
        self._config = config
        self._sleep = sleep
        self._tracker: _CollectingTracker | None = None
        self._downloading: TaskState | None = None  # 进入下载阶段的任务状态，用于中止时生成部分汇总
        self._note: str | None = None

    def current_snapshot(self) -> ProgressSnapshot | None:
        return self._tracker.snapshot if self._tracker else None

    def current_note(self) -> str | None:
        """看板上的临时提示（如扫描阶段限流等待），没有时为 None。"""
        return self._note

    async def run(self, state: TaskState, publish: Publish) -> TaskState:
        try:
            return await self._run(state, publish)
        except (ChannelAccessError, FilterError, DownloadDirMissingError) as exc:
            await self._notify(f"❌ 任务 #{state.task_id} 失败：{exc}")
            return replace(state, status=TaskStatus.FAILED, error=str(exc))
        except asyncio.CancelledError:
            if not await self._send_partial_summary(TaskStatus.CANCELLED, None):
                await self._notify(f"🚫 任务 #{state.task_id} 已取消")
            raise
        except Exception as exc:  # 其余异常交给队列记录并标记失败，但先告知 OWNER
            error = f"{type(exc).__name__}: {exc}"
            if not await self._send_partial_summary(TaskStatus.FAILED, error):
                await self._notify(f"❌ 任务 #{state.task_id} 失败：{error}")
            raise
        finally:
            self._tracker = None
            self._downloading = None
            self._note = None

    async def _run(self, state: TaskState, publish: Publish) -> TaskState:
        spec = state.spec
        ensure_download_root(self._config.download_dir)
        entity = await self._retry_on_flood(state.task_id, lambda: resolve_channel(self._client, spec.link))
        state = replace(state, status=TaskStatus.SCANNING, channel_title=display_title(entity))
        publish(state)

        media_filter = build_filter(spec)
        items = await self._retry_on_flood(state.task_id, lambda: scan(self._client, entity, spec, media_filter))
        if not items:
            await self._notify(f"ℹ️ 任务 #{state.task_id} {state.channel_title} 没有匹配的媒体")
            return replace(state, status=TaskStatus.DONE)
        state = replace(state, status=TaskStatus.DOWNLOADING, items=items)
        publish(state)
        self._downloading = state

        results = await self._download(state, entity)
        final = replace(state, status=TaskStatus.DONE, results=results)
        await self._notify(render_summary(final))
        return final

    async def _retry_on_flood(self, task_id: int, coro_factory: Callable[[], Awaitable[T]]) -> T:
        async def notify(seconds: int) -> None:
            self._note = f"⏳ 任务 #{task_id} 限流，等待 {seconds} 秒后重试"

        try:
            return await _with_flood_retry(coro_factory, notify, self._sleep)
        finally:
            self._note = None

    async def _download(self, state: TaskState, entity: Any) -> tuple[FileResult, ...]:
        tracker = _CollectingTracker(state.task_id, state.channel_title, state.items)
        self._tracker = tracker
        return await download_all(
            self._client,
            entity,
            state.items,
            self._config.download_dir,
            channel_dir_name(entity),
            tracker,
            concurrency=self._config.concurrency,
            max_retries=self._config.max_retries,
        )

    async def _send_partial_summary(self, status: TaskStatus, error: str | None) -> bool:
        """下载阶段中止时发送含已完成文件的汇总；尚未进入下载阶段时返回 False。"""
        if self._downloading is None:
            return False
        results = self._tracker.results if self._tracker else ()
        final = replace(self._downloading, status=status, results=results, error=error)
        await self._notify(render_summary(final))
        return True

    async def _notify(self, text: str) -> None:
        try:
            await asyncio.wait_for(self._notifier.send(text), self._config.notify_timeout)
        except Exception as exc:
            log.warning("发送通知失败，忽略：%s", exc)
