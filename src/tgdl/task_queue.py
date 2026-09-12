"""串行任务队列：状态存储、取消、异常兜底。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Awaitable, Callable

from tgdl.models import ACTIVE_STATUSES, TaskSpec, TaskState, TaskStatus

log = logging.getLogger(__name__)

Publish = Callable[[TaskState], None]
Runner = Callable[[TaskState, Publish], Awaitable[TaskState]]


class TaskQueue:
    def __init__(self, runner: Runner) -> None:
        self._runner = runner
        self._pending: asyncio.Queue[int] = asyncio.Queue()
        self._states: dict[int, TaskState] = {}
        self._next_id = 1
        self._current: asyncio.Task[TaskState] | None = None
        self._current_id: int | None = None
        self._cancel_requested = False

    def submit(self, spec: TaskSpec) -> TaskState:
        state = TaskState(task_id=self._next_id, spec=spec)
        self._next_id += 1
        self._set(state)
        self._pending.put_nowait(state.task_id)
        return state

    def get(self, task_id: int) -> TaskState | None:
        return self._states.get(task_id)

    def active(self) -> tuple[TaskState, ...]:
        return tuple(s for s in sorted(self._states.values(), key=lambda s: s.task_id) if s.status in ACTIVE_STATUSES)

    def current(self) -> TaskState | None:
        return self._states.get(self._current_id) if self._current_id is not None else None

    def cancel(self, task_id: int) -> bool:
        state = self._states.get(task_id)
        if state is None or state.status not in ACTIVE_STATUSES:
            return False
        if task_id == self._current_id and self._current is not None:
            cancelled = self._current.cancel()  # 运行器已结束时返回 False
            self._cancel_requested = cancelled
            return cancelled
        self._set(replace(state, status=TaskStatus.CANCELLED))
        return True

    async def run_forever(self) -> None:
        while True:
            task_id = await self._pending.get()
            await self._run_one(task_id)

    async def _run_one(self, task_id: int) -> None:
        state = self._states[task_id]
        if state.status is not TaskStatus.QUEUED:
            return
        self._current_id, self._cancel_requested = task_id, False
        self._current = asyncio.create_task(self._runner(state, self._set))
        try:
            final = _normalise_final(await self._current)
        except asyncio.CancelledError:
            if not self._cancel_requested:
                raise
            final = replace(self._states[task_id], status=TaskStatus.CANCELLED)
        except Exception as exc:  # 任务失败不能拖垮队列
            log.exception("任务 #%d 异常", task_id)
            final = replace(self._states[task_id], status=TaskStatus.FAILED, error=str(exc))
        finally:
            self._current, self._current_id = None, None
        self._set(final)

    def _set(self, state: TaskState) -> None:
        self._states = {**self._states, state.task_id: state}


def _normalise_final(state: TaskState) -> TaskState:
    """运行器正常返回却仍是活动状态时视为完成，避免任务永远卡在 active 列表。"""
    if state.status not in ACTIVE_STATUSES:
        return state
    log.warning("任务 #%d 运行器返回了活动状态 %s，按已完成处理", state.task_id, state.status.value)
    return replace(state, status=TaskStatus.DONE)
