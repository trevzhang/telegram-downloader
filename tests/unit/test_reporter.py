import asyncio
import logging

import pytest
from telethon.errors import MessageNotModifiedError, RPCError

from tgdl.reporter import ProgressReporter


class _Edit:
    def __init__(self, fail: BaseException | None = None) -> None:
        self.calls: list[str] = []
        self.attempts = 0
        self.fail = fail

    async def __call__(self, text: str) -> None:
        self.attempts += 1
        if self.fail:
            raise self.fail
        self.calls.append(text)


async def test_update_skips_identical_text() -> None:
    edit = _Edit()
    reporter = ProgressReporter(edit)
    assert await reporter.update("a") is True
    assert await reporter.update("a") is False
    assert await reporter.update("b") is True
    assert edit.calls == ["a", "b"]


async def test_update_tolerates_not_modified_error() -> None:
    reporter = ProgressReporter(_Edit(fail=MessageNotModifiedError(request=None)))
    assert await reporter.update("a") is False


async def test_not_modified_remembers_text_and_skips_retry() -> None:
    edit = _Edit(fail=MessageNotModifiedError(request=None))
    reporter = ProgressReporter(edit)
    assert await reporter.update("a") is False
    assert await reporter.update("a") is False
    assert edit.attempts == 1


@pytest.mark.parametrize("error", [ConnectionError("down"), RPCError(request=None, message="x")])
async def test_update_logs_warning_on_edit_failure(error: Exception, caplog: pytest.LogCaptureFixture) -> None:
    reporter = ProgressReporter(_Edit(fail=error))
    with caplog.at_level(logging.WARNING, logger="tgdl.reporter"):
        assert await reporter.update("a") is False
    assert any("编辑进度消息失败" in rec.getMessage() for rec in caplog.records)


async def test_cancelled_error_propagates() -> None:
    reporter = ProgressReporter(_Edit(fail=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await reporter.update("a")


async def test_run_loop_survives_failing_edit() -> None:
    edit = _Edit(fail=ConnectionError("down"))
    reporter = ProgressReporter(edit, interval=0.01)
    stop = asyncio.Event()
    counter = iter(range(100))
    task = asyncio.create_task(reporter.run(lambda: f"tick {next(counter)}", stop))
    await asyncio.sleep(0.05)
    stop.set()
    await task
    assert edit.attempts >= 2


async def test_run_loop_edits_until_stopped_then_final() -> None:
    edit = _Edit()
    reporter = ProgressReporter(edit, interval=0.01)
    stop = asyncio.Event()
    counter = iter(range(100))
    task = asyncio.create_task(reporter.run(lambda: f"tick {next(counter)}", stop))
    await asyncio.sleep(0.05)
    stop.set()
    await task
    assert len(edit.calls) >= 2
    assert edit.calls[-1].startswith("tick")
