import asyncio

from telethon.errors import MessageNotModifiedError

from tgdl.reporter import ProgressReporter


class _Edit:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def __call__(self, text: str) -> None:
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
