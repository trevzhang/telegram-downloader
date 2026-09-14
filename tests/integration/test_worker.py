import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from telethon.errors import ChannelPrivateError, FloodWaitError, RPCError

from tests.fakes.telegram import FakeClient, FakeEntity, FakeFile, FakeMessage, FakeNotifier
from tgdl.models import ChannelRef, FileStatus, TaskSpec, TaskState, TaskStatus
from tgdl.worker import TaskWorker, WorkerConfig

T0 = datetime(2026, 1, 1, tzinfo=UTC)
VIDEO = FakeFile(name="v.mp4", size=8, mime_type="video/mp4", ext=".mp4")


def _client(n: int = 2, **kw: object) -> FakeClient:
    return FakeClient(
        messages=tuple(FakeMessage(id=i, date=T0, message=f"ep{i}", file=VIDEO) for i in range(1, n + 1)),
        entity=FakeEntity(id=1, title="My Chan", username="mychan"),
        **kw,  # type: ignore[arg-type]
    )


def _worker(client: FakeClient, tmp_path: Path, notifier: FakeNotifier, **kw: object) -> TaskWorker:
    config = WorkerConfig(download_dir=tmp_path, concurrency=2, max_retries=0)
    return TaskWorker(client, notifier, config, **kw)  # type: ignore[arg-type]


def _state(**kw: object) -> TaskState:
    return TaskState(task_id=1, spec=TaskSpec(link=ChannelRef(username="mychan"), raw_link="https://t.me/mychan", **kw))  # type: ignore[arg-type]


async def test_happy_path_downloads_and_reports(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    published: list[TaskState] = []
    final = await _worker(_client(3), tmp_path, notifier).run(_state(), published.append)

    assert final.status is TaskStatus.DONE
    assert [r.status for r in final.results] == [FileStatus.DONE] * 3
    assert final.channel_title == "My Chan @mychan"
    assert [s.status for s in published] == [TaskStatus.SCANNING, TaskStatus.DOWNLOADING]
    assert (tmp_path / "My Chan" / "2026_01" / "2 - v.mp4").exists()
    assert notifier.edits == []  # 进度只在看板上，worker 不再编辑任何消息
    assert len(notifier.sent) == 1 and "✅ 成功 3" in notifier.sent[0] and "共 3 个" in notifier.sent[0]


async def test_no_items_finishes_with_message(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    final = await _worker(_client(2), tmp_path, notifier).run(
        _state(filter_expr="caption == 'nomatch'"), lambda s: None
    )
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
    worker = _worker(_client(2, delay=0.05), tmp_path, notifier)
    assert worker.current_snapshot() is None
    task = asyncio.create_task(worker.run(_state(), lambda s: None))
    await asyncio.sleep(0.02)
    snap = worker.current_snapshot()
    assert snap is not None and snap.total_files == 2
    await task
    assert worker.current_snapshot() is None


async def test_cancel_sends_partial_summary_and_reraises(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    worker = _worker(_client(2, delay=0.05), tmp_path, notifier)
    task = asyncio.create_task(worker.run(_state(), lambda s: None))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(notifier.sent) == 1 and "已取消" in notifier.sent[0] and "共 2 个" in notifier.sent[0]
    assert notifier.edits == []
    assert list(tmp_path.rglob("*.part"))  # 取消时保留 .part 供续传


async def test_cancel_before_download_sends_notice(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    client = _client(2)

    async def slow_get_entity(ref: object) -> FakeEntity:
        await asyncio.sleep(1)
        return client.entity  # type: ignore[return-value]

    client.get_entity = slow_get_entity  # type: ignore[method-assign]
    task = asyncio.create_task(_worker(client, tmp_path, notifier).run(_state(), lambda s: None))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any("已取消" in text for text in notifier.sent) and notifier.edits == []


async def test_unexpected_error_notifies_then_propagates(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    client = _client(entity_error=RPCError(None, "boom"))
    with pytest.raises(RPCError):
        await _worker(client, tmp_path, notifier).run(_state(), lambda s: None)
    assert any("失败" in text and "boom" in text for text in notifier.sent)


async def test_download_error_sends_failure_summary_and_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: object, **kwargs: object) -> tuple:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("tgdl.worker.download_all", boom)
    notifier = FakeNotifier()
    with pytest.raises(RuntimeError):
        await _worker(_client(2), tmp_path, notifier).run(_state(), lambda s: None)
    assert len(notifier.sent) == 1 and "失败" in notifier.sent[0] and "kaboom" in notifier.sent[0]
    assert notifier.edits == []


class _SleepSpy:
    def __init__(self, worker_ref: list[TaskWorker] | None = None) -> None:
        self.calls: list[float] = []
        self.notes: list[str | None] = []
        self._worker_ref = worker_ref if worker_ref is not None else []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.notes.extend(w.current_note() for w in self._worker_ref)


async def test_flood_wait_during_resolve_waits_and_retries(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    ref: list[TaskWorker] = []
    sleep = _SleepSpy(ref)
    client = _client(2, entity_errors=[FloodWaitError(request=None, capture=3)])
    worker = _worker(client, tmp_path, notifier, sleep=sleep)
    ref.append(worker)
    final = await worker.run(_state(), lambda s: None)
    assert final.status is TaskStatus.DONE and len(final.results) == 2
    assert sleep.notes and "限流" in (sleep.notes[0] or "") and "3 秒" in (sleep.notes[0] or "")
    assert worker.current_note() is None  # 等待结束后提示清除
    assert not any("限流" in text for text in notifier.sent)  # 限流提示只在看板上，不再单发消息
    assert sleep.calls and sleep.calls[0] >= 3


async def test_flood_wait_over_cap_fails_task_with_clear_message(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    sleep = _SleepSpy()
    client = _client(2, entity_errors=[FloodWaitError(request=None, capture=5000)])
    final = await _worker(client, tmp_path, notifier, sleep=sleep).run(_state(), lambda s: None)
    assert final.status is TaskStatus.FAILED
    assert final.error is not None and "限流等待超过上限" in final.error
    assert sleep.calls == []


async def test_missing_download_dir_fails_task_before_scanning(tmp_path: Path) -> None:
    client = FakeClient(entity=FakeEntity(id=1, title="My Chan"))
    notifier = FakeNotifier()
    worker = _worker(client, tmp_path / "nas-not-mounted", notifier)
    final = await worker.run(_state(), lambda s: None)
    assert final.status == TaskStatus.FAILED
    assert "下载目录不存在" in (final.error or "")
    assert any("下载目录不存在" in text for text in notifier.sent)
    assert not (tmp_path / "nas-not-mounted").exists()


class _HangingNotifier(FakeNotifier):
    async def send(self, text: str) -> int:
        await asyncio.sleep(10)
        return 1

    async def edit(self, message_id: int, text: str) -> None:
        await asyncio.sleep(10)


async def test_hanging_notifier_does_not_block_task(tmp_path: Path) -> None:
    client = _client(1)
    notifier = _HangingNotifier()
    config = WorkerConfig(download_dir=tmp_path, concurrency=1, max_retries=0, notify_timeout=0.01)
    worker = TaskWorker(client, notifier, config)  # type: ignore[arg-type]
    final = await asyncio.wait_for(worker.run(_state(), lambda s: None), timeout=2)
    assert final.status == TaskStatus.DONE
    assert client.download_calls == [1]


def test_display_title_variants() -> None:
    from tgdl.worker import display_title

    assert display_title(FakeEntity(id=1, title="名称", username="user")) == "名称 @user"
    assert display_title(FakeEntity(id=1, title="名称")) == "名称"
    assert display_title(FakeEntity(id=1, username="user")) == "@user"
    assert display_title(FakeEntity(id=7)) == "7"
