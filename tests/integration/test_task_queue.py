import asyncio
from dataclasses import replace

import pytest

from tgdl.models import ChannelRef, TaskSpec, TaskState, TaskStatus
from tgdl.task_queue import TaskQueue

SPEC = TaskSpec(link=ChannelRef(username="c"), raw_link="x")


async def _runner_factory(log: list[int], delay: float = 0.0, fail_on: int | None = None):
    async def runner(state: TaskState, publish) -> TaskState:
        publish(replace(state, status=TaskStatus.SCANNING))
        await asyncio.sleep(delay)
        if state.task_id == fail_on:
            raise RuntimeError("boom")
        log.append(state.task_id)
        return replace(state, status=TaskStatus.DONE)
    return runner


@pytest.fixture
async def running_queue():
    log: list[int] = []
    queue = TaskQueue(await _runner_factory(log, delay=0.02))
    loop_task = asyncio.create_task(queue.run_forever())
    yield queue, log
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass


def test_submit_assigns_incrementing_ids() -> None:
    queue = TaskQueue(runner=None)  # type: ignore[arg-type]
    assert queue.submit(SPEC).task_id == 1
    assert queue.submit(SPEC).task_id == 2
    assert [s.task_id for s in queue.active()] == [1, 2]


async def test_runs_serially_in_order(running_queue) -> None:
    queue, log = running_queue
    queue.submit(SPEC)
    queue.submit(SPEC)
    await asyncio.sleep(0.1)
    assert log == [1, 2]
    assert queue.get(2).status is TaskStatus.DONE
    assert queue.active() == ()


async def test_cancel_queued_task_is_skipped(running_queue) -> None:
    queue, log = running_queue
    queue.submit(SPEC)
    queue.submit(SPEC)
    assert queue.cancel(2) is True
    await asyncio.sleep(0.1)
    assert log == [1]
    assert queue.get(2).status is TaskStatus.CANCELLED


async def test_cancel_running_task(running_queue) -> None:
    queue, log = running_queue
    queue.submit(SPEC)
    await asyncio.sleep(0.005)
    assert queue.current().status is TaskStatus.SCANNING
    assert queue.cancel(1) is True
    await asyncio.sleep(0.05)
    assert queue.get(1).status is TaskStatus.CANCELLED
    assert log == []


async def test_cancel_unknown_or_finished_returns_false(running_queue) -> None:
    queue, _ = running_queue
    assert queue.cancel(42) is False
    queue.submit(SPEC)
    await asyncio.sleep(0.1)
    assert queue.cancel(1) is False


async def test_runner_exception_marks_failed() -> None:
    log: list[int] = []
    queue = TaskQueue(await _runner_factory(log, fail_on=1))
    loop_task = asyncio.create_task(queue.run_forever())
    queue.submit(SPEC)
    queue.submit(SPEC)
    await asyncio.sleep(0.05)
    loop_task.cancel()
    assert queue.get(1).status is TaskStatus.FAILED and "boom" in (queue.get(1).error or "")
    assert queue.get(2).status is TaskStatus.DONE


async def test_shutdown_cancel_propagates() -> None:
    queue = TaskQueue(await _runner_factory([], delay=1.0))
    loop_task = asyncio.create_task(queue.run_forever())
    queue.submit(SPEC)
    await asyncio.sleep(0.01)
    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task
