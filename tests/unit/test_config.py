import pytest

from tgdl.config import ConfigError, load_settings

REQUIRED = {"API_ID": "12345", "API_HASH": "abc", "BOT_TOKEN": "1:x", "OWNER_ID": "42"}
OPTIONAL_KEYS = ("PROXY_HOST", "PROXY_PORT", "PROXY_USERNAME", "PROXY_PASSWORD", "CONCURRENCY")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in OPTIONAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_loads_required_and_defaults(env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)
    assert settings.api_id == 12345
    assert settings.owner_id == 42
    assert settings.concurrency == 3
    assert settings.progress_interval == 5.0


def test_missing_required_raises_config_error(env: pytest.MonkeyPatch) -> None:
    env.delenv("BOT_TOKEN")
    with pytest.raises(ConfigError, match="bot_token"):
        load_settings(env_file=None)


def test_proxy_none_without_host(env: pytest.MonkeyPatch) -> None:
    assert load_settings(env_file=None).proxy() is None


def test_proxy_socks5_dict(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_HOST", "127.0.0.1")
    env.setenv("PROXY_PORT", "7890")
    assert load_settings(env_file=None).proxy() == {
        "proxy_type": "socks5", "addr": "127.0.0.1", "port": 7890, "rdns": True,
    }


def test_proxy_with_auth(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_HOST", "127.0.0.1")
    env.setenv("PROXY_PORT", "1080")
    env.setenv("PROXY_USERNAME", "u")
    env.setenv("PROXY_PASSWORD", "p")
    proxy = load_settings(env_file=None).proxy()
    assert proxy is not None
    assert proxy["username"] == "u" and proxy["password"] == "p"


def test_concurrency_out_of_range(env: pytest.MonkeyPatch) -> None:
    env.setenv("CONCURRENCY", "50")
    with pytest.raises(ConfigError, match="concurrency"):
        load_settings(env_file=None)
