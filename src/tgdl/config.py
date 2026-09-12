"""配置加载：仅从环境变量或 .env 读取，启动时校验必填项。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_core import ErrorDetails
from pydantic_settings import BaseSettings, SettingsConfigDict

PROXY_TYPE_SOCKS5 = "socks5"
MODEL_LEVEL_LOC = "设置"


class ConfigError(RuntimeError):
    """配置缺失或不合法。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    api_id: int
    api_hash: SecretStr
    bot_token: SecretStr
    owner_id: int
    proxy_host: str | None = None
    proxy_port: int | None = Field(default=None, ge=1, le=65535)
    proxy_username: str | None = None
    proxy_password: SecretStr | None = None
    download_dir: Path = Path("downloads")
    data_dir: Path = Path("data")
    concurrency: int = Field(default=3, ge=1, le=10)
    progress_interval: float = Field(default=5.0, ge=1.0)
    max_retries: int = Field(default=3, ge=0, le=10)

    @model_validator(mode="after")
    def _proxy_host_and_port_together(self) -> Settings:
        if (self.proxy_host is None) != (self.proxy_port is None):
            raise ValueError("proxy_host 与 proxy_port 必须同时设置或同时留空")
        return self

    def proxy(self) -> dict[str, Any] | None:
        """返回 Telethon 可用的代理字典；未配置则 None。"""
        if not self.proxy_host or not self.proxy_port:
            return None
        base: dict[str, Any] = {
            "proxy_type": PROXY_TYPE_SOCKS5,
            "addr": self.proxy_host,
            "port": self.proxy_port,
            "rdns": True,
        }
        if self.proxy_username:
            password = self.proxy_password.get_secret_value() if self.proxy_password else ""
            return {**base, "username": self.proxy_username, "password": password}
        return base


def _format_error(err: ErrorDetails) -> str:
    loc = ".".join(str(part) for part in err["loc"]) or MODEL_LEVEL_LOC
    return f"{loc}: {err['msg']}"


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]  # pydantic-settings 的运行时参数未进入签名
    except ValidationError as exc:
        details = ", ".join(_format_error(err) for err in exc.errors())
        raise ConfigError(f"配置无效或缺失: {details}") from exc
