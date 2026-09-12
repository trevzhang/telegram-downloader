"""单个任务的完整执行流程：解析频道 → 扫描 → 并发下载 → 汇总。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from tgdl.bot.notifier import Notifier
from tgdl.downloader import download_all
from tgdl.filters import FilterError, build_filter
from tgdl.models import TaskState, TaskStatus
from tgdl.paths import channel_dir_name
from tgdl.progress import ProgressSnapshot, ProgressTracker, format_bytes, render_progress, render_summary
from tgdl.reporter import ProgressReporter
from tgdl.scanner import ChannelAccessError, resolve_channel, scan

log = logging.getLogger(__name__)

Publish = Callable[[TaskState], None]


@dataclass(frozen=True)
class WorkerConfig:
    download_dir: Path
    concurrency: int
    max_retries: int
    progress_interval: float


def display_title(entity: Any) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"
    return getattr(entity, "title", None) or str(entity.id)


class TaskWorker:
    def __init__(self, user_client: Any, notifier: Notifier, config: WorkerConfig) -> None:
        self._client = user_client
        self._notifier = notifier
        self._config = config
        self._tracker: ProgressTracker | None = None

    def current_snapshot(self) -> ProgressSnapshot | None:
        return self._tracker.snapshot if self._tracker else None

    async def run(self, state: TaskState, publish: Publish) -> TaskState:
        try:
            return await self._run(state, publish)
        except (ChannelAccessError, FilterError) as exc:
            await self._notifier.send(f"❌ 任务 #{state.task_id} 失败：{exc}")
            return replace(state, status=TaskStatus.FAILED, error=str(exc))
        except asyncio.CancelledError:
            await self._notifier.send(f"🚫 任务 #{state.task_id} 已取消")
            raise
        except Exception as exc:  # 其余异常交给队列记录并标记失败，但先告知 OWNER
            await self._notifier.send(f"❌ 任务 #{state.task_id} 失败：{type(exc).__name__}: {exc}")
            raise
        finally:
            self._tracker = None

    async def _run(self, state: TaskState, publish: Publish) -> TaskState:
        spec = state.spec
        await self._notifier.send(f"🔍 任务 #{state.task_id} 开始扫描 {spec.raw_link}")
        entity = await resolve_channel(self._client, spec.link)
        state = replace(state, status=TaskStatus.SCANNING, channel_title=display_title(entity))
        publish(state)

        items = await scan(self._client, entity, spec, build_filter(spec))
        if not items:
            await self._notifier.send(f"ℹ️ 任务 #{state.task_id} 没有匹配的媒体")
            return replace(state, status=TaskStatus.DONE)
        state = replace(state, status=TaskStatus.DOWNLOADING, items=items)
        publish(state)

        total = format_bytes(sum(i.size for i in items))
        message_id = await self._notifier.send(
            f"📋 任务 #{state.task_id}  {state.channel_title}\n共 {len(items)} 个文件，总大小 {total}，开始下载…"
        )
        results = await self._download_with_progress(state, entity, message_id)
        final = replace(state, status=TaskStatus.DONE, results=results)
        await self._notifier.edit(message_id, render_summary(final))
        return final

    async def _download_with_progress(self, state: TaskState, entity: Any, message_id: int) -> tuple:
        tracker = ProgressTracker(state.task_id, state.channel_title, state.items)
        self._tracker = tracker
        reporter = ProgressReporter(lambda text: self._notifier.edit(message_id, text), self._config.progress_interval)
        stop = asyncio.Event()
        report_task = asyncio.create_task(reporter.run(lambda: render_progress(tracker.snapshot), stop))
        try:
            return await download_all(
                self._client, entity, state.items, self._config.download_dir, channel_dir_name(entity),
                tracker, concurrency=self._config.concurrency, max_retries=self._config.max_retries,
            )
        finally:
            stop.set()
            await report_task
