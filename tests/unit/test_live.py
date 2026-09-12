import asyncio
import logging

import pytest

from tests.fakes.telegram import FakeNotifier
from tgdl.bot.live import MAX_LIVE_MESSAGES, LiveMessages


def _live(notifier: FakeNotifier | None = None, **kw: object) -> tuple[LiveMessages, FakeNotifier]:
    notifier = notifier or FakeNotifier()
    return LiveMessages(notifier, interval=0.01, **kw), notifier  # type: ignore[arg-type]


async def test_tick_edits_only_when_text_changes() -> None:
    live, notifier = _live()
    texts = iter(["a", "a", "b"])
    live.register(5, lambda: (next(texts), False), initial_text="a")
    await live.tick()
    assert notifier.edits == []
    await live.tick()
    assert notifier.edits == []
    await live.tick()
    assert notifier.edits == [(5, "b")]


async def test_done_entry_gets_final_edit_and_is_removed() -> None:
    live, notifier = _live()
    live.register(5, lambda: ("final", True), initial_text="x")
    await live.tick()
    assert notifier.edits == [(5, "final")]
    assert live.count == 0
    await live.tick()
    assert notifier.edits == [(5, "final")]


async def test_register_keeps_only_newest_entries() -> None:
    live, _ = _live()
    for i in range(MAX_LIVE_MESSAGES + 2):
        live.register(i, lambda: ("t", False), initial_text="t")
    assert live.count == MAX_LIVE_MESSAGES
    assert live.message_ids == tuple(range(2, MAX_LIVE_MESSAGES + 2))


async def test_edit_failure_and_render_error_are_tolerated(caplog: pytest.LogCaptureFixture) -> None:
    class _Boom(FakeNotifier):
        async def edit(self, message_id: int, text: str) -> None:
            raise ConnectionError("gone")

    live, _ = _live(_Boom())
    live.register(1, lambda: ("new", False), initial_text="old")

    def bad_render() -> tuple[str, bool]:
        raise ValueError("render broke")

    live.register(2, bad_render, initial_text="old")
    with caplog.at_level(logging.WARNING):
        await live.tick()
    assert live.count == 1 and live.message_ids == (1,)
    assert "render broke" in caplog.text and "gone" in caplog.text


async def test_run_loop_ticks_until_cancelled() -> None:
    live, notifier = _live()
    counter = iter(range(100))
    live.register(9, lambda: (f"n{next(counter)}", False), initial_text="")
    task = asyncio.create_task(live.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(notifier.edits) >= 2
