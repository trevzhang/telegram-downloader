import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from telethon.errors import FileIdInvalidError, FileReferenceExpiredError, FloodPremiumWaitError, FloodWaitError

from tests.fakes.telegram import FakeClient, FakeFile, FakeMessage, payload
from tgdl.downloader import download_all, download_item, resume_offset
from tgdl.models import FileStatus, MediaItem, MediaKind
from tgdl.progress import ProgressTracker

T0 = datetime(2026, 1, 1, tzinfo=UTC)
# 非会员账号在 upload.getFile 上会收到 FloodPremiumWaitError，语义与 FloodWaitError 相同
FLOOD_ERRORS = (FloodWaitError, FloodPremiumWaitError)


def _pair(mid: int, size: int = 16) -> tuple[FakeMessage, MediaItem]:
    file = FakeFile(name=f"v{mid}.mp4", size=size, mime_type="video/mp4", ext=".mp4")
    msg = FakeMessage(id=mid, date=T0, file=file)
    item = MediaItem(message_id=mid, date=T0, kind=MediaKind.VIDEO, file_name=file.name or "", size=size)
    return msg, item


async def _noop_sleep(_: float) -> None:
    return None


async def test_downloads_and_renames_part(tmp_path: Path) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,))
    progress: list[tuple[int, int]] = []
    result = await download_item(
        client,
        object(),
        item,
        tmp_path / "a" / "1 - v1.mp4",
        on_progress=lambda c, t: progress.append((c, t)),
        on_flood_wait=lambda s: None,
    )
    assert result.status is FileStatus.DONE
    assert (tmp_path / "a" / "1 - v1.mp4").stat().st_size == 16
    assert not list(tmp_path.rglob("*.part"))
    assert progress[-1] == (16, 16)


async def test_skips_existing_file_with_same_size(tmp_path: Path) -> None:
    msg, item = _pair(1)
    target = tmp_path / "1 - v1.mp4"
    target.write_bytes(b"x" * 16)
    client = FakeClient(messages=(msg,))
    result = await download_item(
        client, object(), item, target, on_progress=lambda c, t: None, on_flood_wait=lambda s: None
    )
    assert result.status is FileStatus.SKIPPED
    assert client.download_calls == []


async def test_retries_then_succeeds(tmp_path: Path) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,), failures=[OSError("net"), OSError("net")])
    result = await download_item(
        client,
        object(),
        item,
        tmp_path / "1.mp4",
        on_progress=lambda c, t: None,
        on_flood_wait=lambda s: None,
        max_retries=3,
        sleep=_noop_sleep,
    )
    assert result.status is FileStatus.DONE
    assert len(client.download_calls) == 3


async def test_retries_exhausted_marks_failed_and_keeps_part(tmp_path: Path) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,), failures=[OSError("net")] * 5)
    result = await download_item(
        client,
        object(),
        item,
        tmp_path / "1.mp4",
        on_progress=lambda c, t: None,
        on_flood_wait=lambda s: None,
        max_retries=2,
        sleep=_noop_sleep,
    )
    assert result.status is FileStatus.FAILED
    assert result.error is not None and "net" in result.error
    assert len(client.download_calls) == 3
    assert (tmp_path / "1.mp4.part").exists()  # 网络错误保留 .part 供下次续传


@pytest.mark.parametrize("error_cls", FLOOD_ERRORS)
async def test_flood_wait_reports_and_retries_without_consuming_attempts(tmp_path: Path, error_cls: type) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,), failures=[error_cls(request=None, capture=7)])
    waits: list[int] = []
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    result = await download_item(
        client,
        object(),
        item,
        tmp_path / "1.mp4",
        on_progress=lambda c, t: None,
        on_flood_wait=waits.append,
        max_retries=0,
        sleep=sleep,
    )
    assert result.status is FileStatus.DONE
    assert waits == [7] and slept and slept[0] >= 7


async def test_missing_message_fails_without_retry(tmp_path: Path) -> None:
    _, item = _pair(99)
    client = FakeClient(messages=())
    result = await download_item(
        client,
        object(),
        item,
        tmp_path / "99.mp4",
        on_progress=lambda c, t: None,
        on_flood_wait=lambda s: None,
        max_retries=3,
        sleep=_noop_sleep,
    )
    assert result.status is FileStatus.FAILED and client.download_calls == []


async def test_cancel_keeps_part_for_resume(tmp_path: Path) -> None:
    msg, item = _pair(1, size=64)
    client = FakeClient(messages=(msg,), delay=0.01)
    task = asyncio.create_task(
        download_item(
            client, object(), item, tmp_path / "1.mp4", on_progress=lambda c, t: None, on_flood_wait=lambda s: None
        )
    )
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    part = tmp_path / "1.mp4.part"
    assert part.exists() and 0 < part.stat().st_size < 64
    assert not (tmp_path / "1.mp4").exists()


async def test_download_all_respects_concurrency_and_updates_tracker(tmp_path: Path) -> None:
    pairs = [_pair(i) for i in range(1, 7)]
    client = FakeClient(messages=tuple(m for m, _ in pairs), delay=0.005)
    items = tuple(i for _, i in pairs)
    tracker = ProgressTracker(task_id=1, channel_title="c", items=items)
    results = await download_all(client, object(), items, tmp_path, "chan", tracker, concurrency=2, max_retries=0)
    assert len(results) == 6 and all(r.status is FileStatus.DONE for r in results)
    assert client.max_concurrent == 2
    assert tracker.snapshot.done == 6 and tracker.snapshot.fraction == 1.0
    assert (tmp_path / "chan" / "2026_01" / "3 - v3.mp4").exists()


async def test_unknown_exception_fails_without_retry_and_siblings_continue(tmp_path: Path) -> None:
    pairs = [_pair(1), _pair(2)]
    client = FakeClient(messages=tuple(m for m, _ in pairs), failures=[ValueError("odd")])
    items = tuple(i for _, i in pairs)
    tracker = ProgressTracker(task_id=1, channel_title="c", items=items)
    results = await download_all(client, object(), items, tmp_path, "chan", tracker, concurrency=1, max_retries=3)
    assert [r.status for r in results] == [FileStatus.FAILED, FileStatus.DONE]
    assert results[0].error == "ValueError: odd"
    assert client.download_calls == [1, 2]
    assert not list(tmp_path.rglob("*.part"))


async def test_expired_file_reference_is_retried(tmp_path: Path) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,), failures=[FileReferenceExpiredError(request=None)])
    result = await download_item(
        client,
        object(),
        item,
        tmp_path / "1.mp4",
        on_progress=lambda c, t: None,
        on_flood_wait=lambda s: None,
        max_retries=3,
        sleep=_noop_sleep,
    )
    assert result.status is FileStatus.DONE
    assert len(client.download_calls) == 2


async def test_permanent_bad_request_fails_immediately(tmp_path: Path) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,), failures=[FileIdInvalidError(request=None)])
    result = await download_item(
        client,
        object(),
        item,
        tmp_path / "1.mp4",
        on_progress=lambda c, t: None,
        on_flood_wait=lambda s: None,
        max_retries=3,
        sleep=_noop_sleep,
    )
    assert result.status is FileStatus.FAILED
    assert result.error is not None and "FileIdInvalidError" in result.error
    assert len(client.download_calls) == 1
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("error_cls", FLOOD_ERRORS)
async def test_flood_wait_beyond_cap_fails_without_sleeping(tmp_path: Path, error_cls: type) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,), failures=[error_cls(request=None, capture=4000)])
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    result = await download_item(
        client,
        object(),
        item,
        tmp_path / "1.mp4",
        on_progress=lambda c, t: None,
        on_flood_wait=lambda s: None,
        max_retries=0,
        sleep=sleep,
    )
    assert result.status is FileStatus.FAILED
    assert result.error is not None and "限流等待超过上限" in result.error
    assert all(s < 4000 for s in slept)
    assert len(client.download_calls) == 1


def test_resume_offset_aligns_and_rejects_oversized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tgdl.downloader.CHUNK_SIZE", 4)
    part = tmp_path / "x.part"
    assert resume_offset(part, 10) == 0
    part.write_bytes(b"1" * 6)
    assert resume_offset(part, 10) == 4
    part.write_bytes(b"1" * 20)
    assert resume_offset(part, 10) == 0
    part.write_bytes(b"1" * 10)
    assert resume_offset(part, 10) == 10


async def _download(client: FakeClient, item: MediaItem, path: Path, progress: list[tuple[int, int]] | None = None):
    return await download_item(
        client,
        object(),
        item,
        path,
        on_progress=lambda c, t: progress.append((c, t)) if progress is not None else None,
        on_flood_wait=lambda s: None,
        max_retries=2,
        sleep=_noop_sleep,
    )


async def test_resumes_from_existing_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tgdl.downloader.CHUNK_SIZE", 4)
    msg, item = _pair(1, size=10)
    (tmp_path / "1.mp4.part").write_bytes(payload(10)[:6])
    client = FakeClient(messages=(msg,))
    progress: list[tuple[int, int]] = []
    result = await _download(client, item, tmp_path / "1.mp4", progress)
    assert result.status is FileStatus.DONE
    assert (tmp_path / "1.mp4").read_bytes() == payload(10)
    assert client.offsets == [4] and progress[0] == (4, 10) and progress[-1] == (10, 10)


async def test_mid_download_failure_keeps_part_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tgdl.downloader.CHUNK_SIZE", 4)
    msg, item = _pair(1, size=10)
    client = FakeClient(messages=(msg,), failures=[OSError("net")], fail_after_chunks=1)
    result = await _download(client, item, tmp_path / "1.mp4")
    assert result.status is FileStatus.DONE
    assert (tmp_path / "1.mp4").read_bytes() == payload(10)
    assert client.offsets == [0, 4]


async def test_oversized_part_restarts_from_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tgdl.downloader.CHUNK_SIZE", 4)
    msg, item = _pair(1, size=10)
    (tmp_path / "1.mp4.part").write_bytes(b"z" * 20)
    client = FakeClient(messages=(msg,))
    result = await _download(client, item, tmp_path / "1.mp4")
    assert result.status is FileStatus.DONE and client.offsets == [0]
    assert (tmp_path / "1.mp4").read_bytes() == payload(10)


async def test_complete_part_is_renamed_without_downloading(tmp_path: Path) -> None:
    msg, item = _pair(1, size=10)
    (tmp_path / "1.mp4.part").write_bytes(payload(10))
    client = FakeClient(messages=(msg,))
    result = await _download(client, item, tmp_path / "1.mp4")
    assert result.status is FileStatus.DONE and client.offsets == []
    assert (tmp_path / "1.mp4").read_bytes() == payload(10) and not (tmp_path / "1.mp4.part").exists()
