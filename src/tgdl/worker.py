"""单个任务的完整执行流程：解析频道 → 扫描 → 并发下载 → 汇总。

通知（send/edit）只是尽力而为：任何发送失败都只记录警告，绝不改变任务结果。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from tgdl.bot.notifier import Notifier
from tgdl.downloader import download_all
from tgdl.filters import FilterError, build_filter
from tgdl.models import FileResult, TaskState, TaskStatus
from tgdl.paths import channel_dir_name
from tgdl.progress import ProgressSnapshot, ProgressTracker, format_bytes, render_progress, render_summary
from tgdl.reporter import ProgressReporter
from tgdl.scanner import ChannelAccessError, resolve_channel, scan

log = logging.getLogger(__name__)

Publish = Callable[[TaskState], None]

UNKNOWN_ENTITY_ID = "?"


@dataclass(frozen=True)
class WorkerConfig:
    download_dir: Path
    concurrency: int
    max_retries: int
    progress_interval: float


@dataclass(frozen=True)
class _Overview:
    """概览消息的 ID（发送失败时为 None）及发送时的任务状态，用于任务中止时生成汇总。"""

    message_id: int | None
    state: TaskState


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


def display_title(entity: Any) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"
    return getattr(entity, "title", None) or str(getattr(entity, "id", UNKNOWN_ENTITY_ID))


class TaskWorker:
    def __init__(self, user_client: Any, notifier: Notifier, config: WorkerConfig) -> None:
        self._client = user_client
        self._notifier = notifier
        self._config = config
        self._tracker: _CollectingTracker | None = None
        self._overview: _Overview | None = None

    def current_snapshot(self) -> ProgressSnapshot | None:
        return self._tracker.snapshot if self._tracker else None

    async def run(self, state: TaskState, publish: Publish) -> TaskState:
        try:
            return await self._run(state, publish)
        except (ChannelAccessError, FilterError) as exc:
            await self._notify(f"❌ 任务 #{state.task_id} 失败：{exc}")
            return replace(state, status=TaskStatus.FAILED, error=str(exc))
        except asyncio.CancelledError:
            if not await self._finalize_overview(TaskStatus.CANCELLED, None):
                await self._notify(f"🚫 任务 #{state.task_id} 已取消")
            raise
        except Exception as exc:  # 其余异常交给队列记录并标记失败，但先告知 OWNER
            error = f"{type(exc).__name__}: {exc}"
            if not await self._finalize_overview(TaskStatus.FAILED, error):
                await self._notify(f"❌ 任务 #{state.task_id} 失败：{error}")
            raise
        finally:
            self._tracker = None
            self._overview = None

    async def _run(self, state: TaskState, publish: Publish) -> TaskState:
        spec = state.spec
        await self._notify(f"🔍 任务 #{state.task_id} 开始扫描 {spec.raw_link}")
        entity = await resolve_channel(self._client, spec.link)
        state = replace(state, status=TaskStatus.SCANNING, channel_title=display_title(entity))
        publish(state)

        items = await scan(self._client, entity, spec, build_filter(spec))
        if not items:
            await self._notify(f"ℹ️ 任务 #{state.task_id} 没有匹配的媒体")
            return replace(state, status=TaskStatus.DONE)
        state = replace(state, status=TaskStatus.DOWNLOADING, items=items)
        publish(state)

        total = format_bytes(sum(i.size for i in items))
        message_id = await self._try_send(
            f"📋 任务 #{state.task_id}  {state.channel_title}\n共 {len(items)} 个文件，总大小 {total}，开始下载…"
        )
        self._overview = _Overview(message_id=message_id, state=state)
        results = await self._download_with_progress(state, entity, message_id)
        final = replace(state, status=TaskStatus.DONE, results=results)
        await self._finalize_message(message_id, render_summary(final))
        return final

    async def _download_with_progress(
        self, state: TaskState, entity: Any, message_id: int | None,
    ) -> tuple[FileResult, ...]:
        tracker = _CollectingTracker(state.task_id, state.channel_title, state.items)
        self._tracker = tracker
        reporter = ProgressReporter(lambda text: self._edit_progress(message_id, text), self._config.progress_interval)
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

    async def _edit_progress(self, message_id: int | None, text: str) -> None:
        """概览消息发送失败时没有可编辑的目标，进度刷新直接跳过；其余失败由 Reporter 记录。"""
        if message_id is not None:
            await self._notifier.edit(message_id, text)

    async def _finalize_overview(self, status: TaskStatus, error: str | None) -> bool:
        """任务中止时把概览消息改写为汇总（含已完成文件）；没有概览消息时返回 False。"""
        if self._overview is None:
            return False
        results = self._tracker.results if self._tracker else ()
        final = replace(self._overview.state, status=status, results=results, error=error)
        await self._finalize_message(self._overview.message_id, render_summary(final))
        return True

    async def _try_send(self, text: str) -> int | None:
        try:
            return await self._notifier.send(text)
        except Exception as exc:
            log.warning("发送通知失败，忽略：%s", exc)
            return None

    async def _notify(self, text: str) -> None:
        await self._try_send(text)

    async def _finalize_message(self, message_id: int | None, text: str) -> None:
        """优先编辑已有消息；消息不存在或编辑失败时改为重新发送，失败同样只记录警告。"""
        if message_id is not None:
            try:
                await self._notifier.edit(message_id, text)
                return
            except Exception as exc:
                log.warning("编辑消息 %s 失败，改为重新发送：%s", message_id, exc)
        await self._notify(text)
