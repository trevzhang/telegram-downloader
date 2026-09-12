"""配置加载：仅从环境变量或 .env 读取，启动时校验必填项。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

PROXY_TYPE_SOCKS5 = "socks5"


class ConfigError(RuntimeError):
    """配置缺失或不合法。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_id: int
    api_hash: str
    bot_token: str
    owner_id: int
    proxy_host: str | None = None
    proxy_port: int | None = Field(default=None, ge=1, le=65535)
    proxy_username: str | None = None
    proxy_password: str | None = None
    download_dir: Path = Path("downloads")
    data_dir: Path = Path("data")
    concurrency: int = Field(default=3, ge=1, le=10)
    progress_interval: float = Field(default=5.0, ge=1.0)
    max_retries: int = Field(default=3, ge=0, le=10)

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
            return {**base, "username": self.proxy_username, "password": self.proxy_password or ""}
        return base


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        fields = ", ".join(".".join(str(part) for part in err["loc"]) for err in exc.errors())
        raise ConfigError(f"配置无效或缺失: {fields}") from exc
