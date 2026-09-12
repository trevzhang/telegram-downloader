"""单文件下载（跳过/重试/限流/取消清理）与任务级并发下载。"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from telethon.errors import FloodWaitError, RPCError

from tgdl.models import FileResult, FileStatus, MediaItem
from tgdl.paths import part_path, target_path
from tgdl.progress import ProgressTracker

log = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 2.0
FLOOD_WAIT_MARGIN_SECONDS = 1

ProgressFn = Callable[[int, int], None]
FloodWaitFn = Callable[[int], None]
SleepFn = Callable[[float], Awaitable[None]]


class MediaUnavailableError(RuntimeError):
    """消息已被删除或无法获取，不重试。"""


def _is_complete(path: Path, item: MediaItem) -> bool:
    return path.exists() and path.stat().st_size == item.size


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
    attempt = 0
    while True:
        try:
            await _download_once(client, entity, item, part, on_progress)
            part.replace(path)
            return FileResult(item=item, path=path, status=FileStatus.DONE)
        except FloodWaitError as exc:
            log.warning("限流 %d 秒: %s", exc.seconds, item.file_name)
            on_flood_wait(exc.seconds)
            await sleep(exc.seconds + FLOOD_WAIT_MARGIN_SECONDS)
        except asyncio.CancelledError:
            part.unlink(missing_ok=True)
            raise
        except MediaUnavailableError as exc:
            part.unlink(missing_ok=True)
            return FileResult(item=item, path=path, status=FileStatus.FAILED, error=str(exc))
        except (OSError, RPCError) as exc:
            part.unlink(missing_ok=True)
            if attempt >= max_retries:
                log.error("下载失败 %s: %s", item.file_name, exc)
                return FileResult(item=item, path=path, status=FileStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            attempt += 1
            await sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))


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
