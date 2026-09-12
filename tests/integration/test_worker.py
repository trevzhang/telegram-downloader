import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from telethon.errors import ChannelPrivateError, RPCError

from tgdl.models import ChannelRef, FileStatus, TaskSpec, TaskState, TaskStatus
from tgdl.worker import TaskWorker, WorkerConfig
from tests.fakes.telegram import FakeClient, FakeEntity, FakeFile, FakeMessage, FakeNotifier

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
VIDEO = FakeFile(name="v.mp4", size=8, mime_type="video/mp4", ext=".mp4")


def _client(n: int = 2, **kw: object) -> FakeClient:
    return FakeClient(
        messages=tuple(FakeMessage(id=i, date=T0, message=f"ep{i}", file=VIDEO) for i in range(1, n + 1)),
        entity=FakeEntity(id=1, title="My Chan", username="mychan"), **kw,  # type: ignore[arg-type]
    )


def _worker(client: FakeClient, tmp_path: Path, notifier: FakeNotifier) -> TaskWorker:
    config = WorkerConfig(download_dir=tmp_path, concurrency=2, max_retries=0, progress_interval=0.01)
    return TaskWorker(client, notifier, config)


def _state(**kw: object) -> TaskState:
    return TaskState(task_id=1, spec=TaskSpec(link=ChannelRef(username="mychan"), raw_link="https://t.me/mychan", **kw))  # type: ignore[arg-type]


async def test_happy_path_downloads_and_reports(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    published: list[TaskState] = []
    final = await _worker(_client(3), tmp_path, notifier).run(_state(), published.append)

    assert final.status is TaskStatus.DONE
    assert [r.status for r in final.results] == [FileStatus.DONE] * 3
    assert final.channel_title == "@mychan"
    assert [s.status for s in published] == [TaskStatus.SCANNING, TaskStatus.DOWNLOADING]
    assert (tmp_path / "mychan" / "2026-01" / "2_v.mp4").exists()
    assert any("共 3 个文件" in text for text in notifier.sent)
    assert notifier.edits and "成功：3" in notifier.edits[-1][1]


async def test_no_items_finishes_with_message(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    final = await _worker(_client(2), tmp_path, notifier).run(_state(regex="nomatch"), lambda s: None)
    assert final.status is TaskStatus.DONE and final.results == ()
    assert any("没有匹配" in text for text in notifier.sent)


async def test_channel_access_error_fails_gracefully(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    client = _client(entity_error=ChannelPrivateError(request=None))
    final = await _worker(client, tmp_path, notifier).run(_state(), lambda s: None)
    assert final.status is TaskStatus.FAILED
    assert final.error is not None and "无权访问" in final.error
    assert any("无权访问" in text for text in notifier.sent)


async def test_current_snapshot_available_during_download(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    worker = _worker(_client(2, delay=0.01), tmp_path, notifier)
    assert worker.current_snapshot() is None
    task = asyncio.create_task(worker.run(_state(), lambda s: None))
    await asyncio.sleep(0.02)
    snap = worker.current_snapshot()
    assert snap is not None and snap.total_files == 2
    await task
    assert worker.current_snapshot() is None


async def test_cancel_sends_notice_and_reraises(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    worker = _worker(_client(2, delay=0.05), tmp_path, notifier)
    task = asyncio.create_task(worker.run(_state(), lambda s: None))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert any("已取消" in text for _, text in notifier.edits) or any("已取消" in t for t in notifier.sent)
    assert not list(tmp_path.rglob("*.part"))


async def test_unexpected_error_notifies_then_propagates(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    client = _client(entity_error=RPCError(None, "boom"))
    with pytest.raises(RPCError):
        await _worker(client, tmp_path, notifier).run(_state(), lambda s: None)
    assert any("失败" in text and "boom" in text for text in notifier.sent)
