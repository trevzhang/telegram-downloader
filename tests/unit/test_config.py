import pytest

from tgdl.config import ConfigError, Settings, load_settings

REQUIRED = {"API_ID": "12345", "API_HASH": "abc", "BOT_TOKEN": "1:x", "OWNER_ID": "42"}
ALL_KEYS = tuple(name.upper() for name in Settings.model_fields)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in ALL_KEYS:
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


def test_blank_optional_env_treated_as_unset(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_HOST", "")
    env.setenv("PROXY_PORT", "")
    assert load_settings(env_file=None).proxy() is None


def test_proxy_socks5_dict(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_HOST", "127.0.0.1")
    env.setenv("PROXY_PORT", "7890")
    assert load_settings(env_file=None).proxy() == {
        "proxy_type": "socks5",
        "addr": "127.0.0.1",
        "port": 7890,
        "rdns": True,
    }


def test_proxy_with_auth(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_HOST", "127.0.0.1")
    env.setenv("PROXY_PORT", "1080")
    env.setenv("PROXY_USERNAME", "u")
    env.setenv("PROXY_PASSWORD", "p")
    proxy = load_settings(env_file=None).proxy()
    assert proxy is not None
    assert proxy["username"] == "u" and proxy["password"] == "p"


def test_proxy_host_without_port_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_HOST", "127.0.0.1")
    with pytest.raises(ConfigError, match="同时设置"):
        load_settings(env_file=None)


def test_proxy_port_without_host_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_PORT", "1080")
    with pytest.raises(ConfigError, match="同时设置"):
        load_settings(env_file=None)


def test_concurrency_out_of_range(env: pytest.MonkeyPatch) -> None:
    env.setenv("CONCURRENCY", "50")
    with pytest.raises(ConfigError, match="concurrency") as info:
        load_settings(env_file=None)
    assert "10" in str(info.value)


def test_secrets_not_in_repr(env: pytest.MonkeyPatch) -> None:
    env.setenv("PROXY_HOST", "127.0.0.1")
    env.setenv("PROXY_PORT", "1080")
    env.setenv("PROXY_PASSWORD", "secret-pw")
    text = repr(load_settings(env_file=None))
    assert REQUIRED["API_HASH"] not in text
    assert REQUIRED["BOT_TOKEN"] not in text
    assert "secret-pw" not in text
