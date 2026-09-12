import asyncio
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tgdl.config import ConfigError, Settings
from tgdl.main import (
    BOT_SESSION_NAME,
    USER_SESSION_NAME,
    _run_until_first_done,
    bot_id_from_token,
    build_clients,
    build_worker_config,
    session_paths,
    start_clients,
)


def _settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(
        _env_file=None,
        api_id=1,
        api_hash="h",
        bot_token="42:secret",
        owner_id=1,
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        concurrency=4,
        **kw,
    )  # type: ignore[arg-type]


def test_session_paths_under_data_dir(tmp_path: Path) -> None:
    user, bot = session_paths(_settings(tmp_path))
    assert user == str(tmp_path / "data" / USER_SESSION_NAME)
    assert bot == str(tmp_path / "data" / BOT_SESSION_NAME)


def test_build_worker_config(tmp_path: Path) -> None:
    config = build_worker_config(_settings(tmp_path))
    assert config.download_dir == tmp_path / "dl"
    assert config.concurrency == 4


class _RecordingClient:
    def __init__(self, session: str, api_id: int, api_hash: str, **kwargs: Any) -> None:
        self.session, self.api_id, self.api_hash, self.kwargs = session, api_id, api_hash, kwargs


def test_build_clients_passes_proxy_and_flood_threshold_only_to_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tgdl.main.TelegramClient", _RecordingClient)
    settings = _settings(tmp_path, proxy_host="127.0.0.1", proxy_port=7890)
    user, bot = build_clients(settings)
    assert (user.session, bot.session) == (str(tmp_path / "data" / "user"), str(tmp_path / "data" / "bot"))
    assert (user.api_id, user.api_hash) == (1, "h") and (bot.api_id, bot.api_hash) == (1, "h")
    assert user.kwargs["proxy"] == settings.proxy() and user.kwargs["flood_sleep_threshold"] == 0
    assert bot.kwargs["proxy"] == settings.proxy() and "flood_sleep_threshold" not in bot.kwargs
    assert (tmp_path / "data").is_dir()  # session 目录在构造客户端前已创建


@pytest.mark.parametrize("token", ["42:abc", "42"])
def test_bot_id_from_token(token: str) -> None:
    if ":" in token:
        assert bot_id_from_token(token) == 42
    else:
        with pytest.raises(ConfigError, match="BOT_TOKEN"):
            bot_id_from_token(token)


@dataclass
class _Me:
    id: int


@dataclass
class _FakeClient:
    authorized: bool = True
    me_id: int = 42
    calls: list[str] = field(default_factory=list)
    bot_token: str | None = None

    async def connect(self) -> None:
        self.calls.append("connect")

    async def is_user_authorized(self) -> bool:
        self.calls.append("is_user_authorized")
        return self.authorized

    async def start(self, bot_token: str | None = None) -> None:
        self.calls.append("start")
        self.bot_token = bot_token

    async def get_me(self) -> _Me:
        return _Me(self.me_id)


async def test_start_clients_happy_path(tmp_path: Path) -> None:
    user, bot = _FakeClient(), _FakeClient()
    await start_clients(user, bot, _settings(tmp_path))  # type: ignore[arg-type]
    assert user.calls == ["connect", "is_user_authorized", "start"]
    assert bot.calls == ["start"] and bot.bot_token == "42:secret"


async def test_start_clients_refuses_first_login_without_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    user, bot = _FakeClient(authorized=False), _FakeClient()
    with pytest.raises(ConfigError, match="交互终端"):
        await start_clients(user, bot, _settings(tmp_path))  # type: ignore[arg-type]
    assert "start" not in user.calls and bot.calls == []


async def test_start_clients_detects_bot_session_mismatch(tmp_path: Path) -> None:
    user, bot = _FakeClient(), _FakeClient(me_id=7)
    with pytest.raises(ConfigError, match="另一个 Bot"):
        await start_clients(user, bot, _settings(tmp_path))  # type: ignore[arg-type]


async def test_run_until_first_done_reraises_queue_error_and_cancels_bot() -> None:
    cancelled = asyncio.Event()

    async def failing_queue() -> None:
        raise RuntimeError("queue boom")

    async def bot_forever() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(RuntimeError, match="queue boom"):
        await _run_until_first_done(failing_queue(), bot_forever())
    assert cancelled.is_set()


async def test_run_until_first_done_returns_when_bot_disconnects() -> None:
    async def queue_forever() -> None:
        await asyncio.sleep(10)

    async def bot_done() -> None:
        return None

    await _run_until_first_done(queue_forever(), bot_done())
