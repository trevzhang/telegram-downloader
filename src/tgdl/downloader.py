"""单文件下载（跳过/重试/限流/取消清理）与任务级并发下载。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from telethon.errors import (
    BadRequestError,
    FileReferenceExpiredError,
    FloodPremiumWaitError,
    FloodWaitError,
    RPCError,
)

from tgdl.models import FileResult, FileStatus, MediaItem
from tgdl.paths import part_path, target_path
from tgdl.progress import ProgressTracker

log = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 2.0
FLOOD_WAIT_MARGIN_SECONDS = 1
MAX_FLOOD_WAIT_TOTAL_SECONDS = 3600  # 单个文件累计限流等待上限
FLOOD_WAIT_CAP_ERROR = "限流等待超过上限"
# BadRequest 一般是永久性错误（文件 ID 无效等），仅文件引用过期可通过重新取消息修复
RETRYABLE_BAD_REQUESTS: tuple[type[BadRequestError], ...] = (FileReferenceExpiredError,)
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (OSError, RPCError)
# 非会员账号在 upload.getFile 上会收到 FloodPremiumWaitError，它不是 FloodWaitError 的子类但同样带 .seconds
FLOOD_ERRORS: tuple[type[RPCError], ...] = (FloodWaitError, FloodPremiumWaitError)

ProgressFn = Callable[[int, int], None]
FloodWaitFn = Callable[[int], None]
SleepFn = Callable[[float], Awaitable[None]]


class MediaUnavailableError(RuntimeError):
    """消息已被删除或无法获取，不重试。"""


@dataclass(frozen=True)
class _Attempt:
    retries: int = 0
    flood_waited: int = 0


def _is_complete(path: Path, item: MediaItem) -> bool:
    return path.exists() and path.stat().st_size == item.size


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, BadRequestError):
        return isinstance(exc, RETRYABLE_BAD_REQUESTS)
    return isinstance(exc, TRANSIENT_ERRORS)


def _failed(item: MediaItem, path: Path, error: str) -> FileResult:
    return FileResult(item=item, path=path, status=FileStatus.FAILED, error=error)


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _log_final_failure(exc: Exception, item: MediaItem) -> None:
    if isinstance(exc, TRANSIENT_ERRORS):
        log.error("下载失败 %s: %s", item.file_name, _describe(exc))
    else:
        log.exception("下载 %s 时遇到未预期异常", item.file_name)


async def _wait_flood(exc: FloodWaitError | FloodPremiumWaitError, attempt: _Attempt, item: MediaItem,
                      on_flood_wait: FloodWaitFn, sleep: SleepFn) -> _Attempt | None:
    """执行限流等待；累计超过上限时返回 None。"""
    waited = attempt.flood_waited + exc.seconds
    if waited > MAX_FLOOD_WAIT_TOTAL_SECONDS:
        log.error("限流 %d 秒，累计 %d 秒超过上限: %s", exc.seconds, waited, item.file_name)
        return None
    log.warning("限流 %d 秒: %s", exc.seconds, item.file_name)
    on_flood_wait(exc.seconds)
    await sleep(exc.seconds + FLOOD_WAIT_MARGIN_SECONDS)
    return replace(attempt, flood_waited=waited)


async def _download_once(client: Any, entity: Any, item: MediaItem, part: Path, on_progress: ProgressFn) -> None:
    message = await client.get_messages(entity, ids=item.message_id)
    if message is None:
        raise MediaUnavailableError(f"消息 {item.message_id} 已不存在")
    await client.download_media(message, file=str(part), progress_callback=on_progress)


async def download_item(
    client: Any, entity: Any, item: MediaItem, path: Path, *,
    on_progress: ProgressFn, on_flood_wait: FloodWaitFn,
    max_retries: int = 3, sleep: SleepFn = asyncio.sleep,
) -> FileResult:
    if _is_complete(path, item):
        return FileResult(item=item, path=path, status=FileStatus.SKIPPED)
    part = part_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    attempt = _Attempt()
    while True:
        try:
            await _download_once(client, entity, item, part, on_progress)
            part.replace(path)
            return FileResult(item=item, path=path, status=FileStatus.DONE)
        except asyncio.CancelledError:
            part.unlink(missing_ok=True)
            raise
        except FLOOD_ERRORS as exc:
            part.unlink(missing_ok=True)
            next_attempt = await _wait_flood(exc, attempt, item, on_flood_wait, sleep)
            if next_attempt is None:
                return _failed(item, path, FLOOD_WAIT_CAP_ERROR)
            attempt = next_attempt
        except MediaUnavailableError as exc:
            part.unlink(missing_ok=True)
            return _failed(item, path, str(exc))
        except Exception as exc:  # 任何未知异常都不能逃出单文件下载，否则会拖垮整个任务
            part.unlink(missing_ok=True)
            if not _is_retryable(exc) or attempt.retries >= max_retries:
                _log_final_failure(exc, item)
                return _failed(item, path, _describe(exc))
            attempt = replace(attempt, retries=attempt.retries + 1)
            await sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt.retries - 1))


async def download_all(
    client: Any, entity: Any, items: tuple[MediaItem, ...], root: Path, channel_dir: str,
    tracker: ProgressTracker, *, concurrency: int, max_retries: int,
) -> tuple[FileResult, ...]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(item: MediaItem) -> FileResult:
        async with semaphore:
            result = await download_item(
                client, entity, item, target_path(root, channel_dir, item),
                on_progress=lambda cur, total: tracker.on_file_progress(item.message_id, item.file_name, cur, total),
                on_flood_wait=tracker.on_flood_wait, max_retries=max_retries,
            )
            tracker.on_file_done(result)
            return result

    return tuple(await asyncio.gather(*(one(item) for item in items)))
