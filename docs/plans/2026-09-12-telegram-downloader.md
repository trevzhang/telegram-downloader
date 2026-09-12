# Telegram 媒体下载器 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现一个「用户账号抓取 + Bot 交互」的 Telegram 频道/群组视频图片下载器，支持正则/时间/序号过滤、SOCKS5 代理和实时进度推送。

**Architecture:** 单进程 asyncio，两个 Telethon 客户端：user_client 负责解析链接、扫描消息、下载媒体；bot_client 负责接收 OWNER 命令并编辑进度消息。任务串行排队，任务内文件以信号量并发下载。所有状态对象为 frozen dataclass，通过 `dataclasses.replace` 更新。

**Tech Stack:** Python 3.11+、uv、Telethon 1.45、python-socks、pydantic-settings、pytest + pytest-asyncio + pytest-cov。

**设计文档:** `docs/plans/2026-09-12-telegram-downloader-design.md`

**通用约定:**
- 所有命令在项目根目录执行，`uv run` 前缀保证使用项目虚拟环境。
- 每个任务严格 TDD：先写测试 → 运行确认失败 → 最小实现 → 运行确认通过 → 提交。
- 提交信息格式 `<type>: <中文描述>`，不加 Co-Authored-By。
- 每个源文件不超过 300 行，函数不超过 50 行。
- 不修改传入对象；局部累加用的列表可以，但返回值统一转 tuple。

---

## 模块依赖顺序

```
models ─┬─ config
        ├─ link_parser ─┐
        ├─ filters ─────┼─ bot/commands
        ├─ paths        │
        ├─ progress ─── reporter
        ├─ scanner (link_parser, paths, filters)
        ├─ downloader (paths, progress)
        ├─ task_queue
        └─ worker (scanner, downloader, progress, reporter) ─ bot/handlers ─ main
```

---

### Task 0: 项目骨架与工具链

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `src/tgdl/__init__.py`
- Create: `src/tgdl/bot/__init__.py`
- Create: `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/fakes/__init__.py`
- Create: `tests/unit/test_smoke.py`

**Step 1: 写 pyproject.toml**

```toml
[project]
name = "tgdl"
version = "0.1.0"
description = "Telegram 频道/群组视频图片下载器（用户账号抓取 + Bot 进度推送）"
requires-python = ">=3.11"
dependencies = [
    "telethon>=1.45,<2",
    "python-socks[asyncio]>=2.4",
    "pydantic-settings>=2.6",
]

[project.scripts]
tgdl = "tgdl.main:run"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/tgdl"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]

[tool.coverage.run]
source = ["tgdl"]
omit = ["src/tgdl/main.py", "src/tgdl/logging_setup.py", "src/tgdl/bot/notifier.py"]
```

**Step 2: 写 .env.example**

```dotenv
# 从 https://my.telegram.org 申请
API_ID=123456
API_HASH=your_api_hash
# 从 @BotFather 获取
BOT_TOKEN=123456:ABC-DEF
# 你自己的 Telegram 用户 ID（可通过 @userinfobot 查询），只有此用户能操作 Bot
OWNER_ID=123456789

# SOCKS5 代理，留空则直连
PROXY_HOST=127.0.0.1
PROXY_PORT=7890
PROXY_USERNAME=
PROXY_PASSWORD=

DOWNLOAD_DIR=downloads
DATA_DIR=data
# 任务内并发下载文件数 1-10
CONCURRENCY=3
# 进度消息编辑间隔（秒）
PROGRESS_INTERVAL=5
MAX_RETRIES=3
```

**Step 3: 写包初始化文件与冒烟测试**

`src/tgdl/__init__.py`:
```python
"""tgdl：Telegram 频道/群组媒体下载器。"""

__version__ = "0.1.0"
```

`src/tgdl/bot/__init__.py`、`tests/__init__.py`、`tests/unit/__init__.py`、`tests/integration/__init__.py`、`tests/fakes/__init__.py` 均为空文件。

`tests/unit/test_smoke.py`:
```python
import tgdl


def test_package_importable() -> None:
    assert tgdl.__version__ == "0.1.0"
```

**Step 4: 安装依赖并运行测试**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv sync && uv run pytest -q`
Expected: `1 passed`

**Step 5: 补充 .gitignore 并提交**

在 `.gitignore` 追加一行 `uv.lock` 不要忽略（保留锁文件），确认 `.venv/` 已忽略。

```bash
git add pyproject.toml uv.lock .env.example src tests
git commit -m "chore: 初始化项目骨架与测试工具链"
```

---

### Task 1: 领域模型 models.py

**Files:**
- Create: `src/tgdl/models.py`
- Test: `tests/unit/test_models.py`

**Step 1: 写失败测试**

```python
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tgdl.models import (
    ChannelRef, FileResult, FileStatus, MediaItem, MediaKind, TaskSpec, TaskState, TaskStatus,
)


def _spec() -> TaskSpec:
    return TaskSpec(link=ChannelRef(username="chan"), raw_link="https://t.me/chan")


def test_task_spec_is_frozen() -> None:
    spec = _spec()
    with pytest.raises(FrozenInstanceError):
        spec.regex = "x"  # type: ignore[misc]


def test_task_state_defaults() -> None:
    state = TaskState(task_id=1, spec=_spec())
    assert state.status is TaskStatus.QUEUED
    assert state.items == ()
    assert state.results == ()


def test_replace_creates_new_state() -> None:
    state = TaskState(task_id=1, spec=_spec())
    updated = replace(state, status=TaskStatus.DONE)
    assert state.status is TaskStatus.QUEUED
    assert updated.status is TaskStatus.DONE


def test_media_kind_values() -> None:
    assert MediaKind("video") is MediaKind.VIDEO
    assert {k.value for k in MediaKind} == {"video", "photo", "all"}


def test_file_result_holds_item() -> None:
    item = MediaItem(message_id=5, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     kind=MediaKind.PHOTO, file_name="a.jpg", size=10)
    result = FileResult(item=item, path=Path("a.jpg"), status=FileStatus.DONE)
    assert result.error is None
    assert result.item.message_id == 5
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_models.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'tgdl.models'`

**Step 3: 最小实现**

`src/tgdl/models.py`:
```python
"""领域模型：全部为不可变 dataclass，更新一律用 dataclasses.replace。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class MediaKind(str, Enum):
    VIDEO = "video"
    PHOTO = "photo"
    ALL = "all"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    SCANNING = "scanning"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FileStatus(str, Enum):
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


ACTIVE_STATUSES = frozenset({TaskStatus.QUEUED, TaskStatus.SCANNING, TaskStatus.DOWNLOADING})


@dataclass(frozen=True)
class ChannelRef:
    """解析后的频道引用。三种标识互斥：username / channel_id / invite_hash。"""

    username: str | None = None
    channel_id: int | None = None
    invite_hash: str | None = None
    message_id: int | None = None


@dataclass(frozen=True)
class TaskSpec:
    link: ChannelRef
    raw_link: str
    regex: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    id_from: int | None = None
    id_to: int | None = None
    kind: MediaKind = MediaKind.ALL


@dataclass(frozen=True)
class MediaItem:
    message_id: int
    date: datetime
    kind: MediaKind
    file_name: str
    size: int
    caption: str = ""


@dataclass(frozen=True)
class FileResult:
    item: MediaItem
    path: Path
    status: FileStatus
    error: str | None = None


@dataclass(frozen=True)
class TaskState:
    task_id: int
    spec: TaskSpec
    status: TaskStatus = TaskStatus.QUEUED
    channel_title: str = ""
    items: tuple[MediaItem, ...] = ()
    results: tuple[FileResult, ...] = ()
    error: str | None = None
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/unit/test_models.py -q`
Expected: `5 passed`

**Step 5: 提交**

```bash
git add src/tgdl/models.py tests/unit/test_models.py
git commit -m "feat: 添加不可变领域模型"
```

---

### Task 2: 配置加载 config.py

**Files:**
- Create: `src/tgdl/config.py`
- Test: `tests/unit/test_config.py`

**Step 1: 写失败测试**

```python
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
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'tgdl.config'`

**Step 3: 最小实现**

`src/tgdl/config.py`:
```python
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
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/unit/test_config.py -q`
Expected: `6 passed`

**Step 5: 提交**

```bash
git add src/tgdl/config.py tests/unit/test_config.py
git commit -m "feat: 添加基于 pydantic-settings 的配置加载"
```

---

### Task 3: 链接解析 link_parser.py

**Files:**
- Create: `src/tgdl/link_parser.py`
- Test: `tests/unit/test_link_parser.py`

**Step 1: 写失败测试**

```python
import pytest

from tgdl.link_parser import LinkParseError, parse_link
from tgdl.models import ChannelRef


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://t.me/somechan", ChannelRef(username="somechan")),
        ("http://t.me/somechan/", ChannelRef(username="somechan")),
        ("t.me/somechan/123", ChannelRef(username="somechan", message_id=123)),
        ("https://t.me/somechan/123?single", ChannelRef(username="somechan", message_id=123)),
        ("https://telegram.me/some_chan", ChannelRef(username="some_chan")),
        ("@somechan", ChannelRef(username="somechan")),
        ("https://t.me/c/1234567890/55", ChannelRef(channel_id=1234567890, message_id=55)),
        ("https://t.me/c/1234567890", ChannelRef(channel_id=1234567890)),
        ("https://t.me/+AbCdEf_-123", ChannelRef(invite_hash="AbCdEf_-123")),
        ("https://t.me/joinchat/AbCdEf", ChannelRef(invite_hash="AbCdEf")),
    ],
)
def test_parse_valid_links(raw: str, expected: ChannelRef) -> None:
    assert parse_link(raw) == expected


@pytest.mark.parametrize("raw", ["", "hello", "https://example.com/x", "https://t.me/", "https://t.me/ab"])
def test_parse_invalid_links(raw: str) -> None:
    with pytest.raises(LinkParseError):
        parse_link(raw)
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_link_parser.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 最小实现**

`src/tgdl/link_parser.py`:
```python
"""解析 Telegram 复制链接为 ChannelRef。"""
from __future__ import annotations

import re

from tgdl.models import ChannelRef


class LinkParseError(ValueError):
    """无法识别的链接。"""


_HOST = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(.+)$", re.IGNORECASE)
_PRIVATE = re.compile(r"^c/(\d+)(?:/(\d+))?$")
_INVITE = re.compile(r"^(?:\+|joinchat/)([A-Za-z0-9_-]+)$")
_PUBLIC = re.compile(r"^([A-Za-z][A-Za-z0-9_]{3,31})(?:/(\d+))?$")


def _opt_int(value: str | None) -> int | None:
    return int(value) if value else None


def _extract_path(raw: str) -> str:
    text = raw.strip()
    match = _HOST.match(text)
    if match:
        path = match.group(1)
    elif text.startswith("@"):
        path = text[1:]
    else:
        raise LinkParseError(f"无法识别的链接: {raw!r}")
    return path.split("?", 1)[0].rstrip("/")


def parse_link(raw: str) -> ChannelRef:
    path = _extract_path(raw)
    if match := _PRIVATE.match(path):
        return ChannelRef(channel_id=int(match.group(1)), message_id=_opt_int(match.group(2)))
    if match := _INVITE.match(path):
        return ChannelRef(invite_hash=match.group(1))
    if match := _PUBLIC.match(path):
        return ChannelRef(username=match.group(1), message_id=_opt_int(match.group(2)))
    raise LinkParseError(f"无法识别的链接: {raw!r}")
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/unit/test_link_parser.py -q`
Expected: `15 passed`

**Step 5: 提交**

```bash
git add src/tgdl/link_parser.py tests/unit/test_link_parser.py
git commit -m "feat: 添加 t.me 链接解析"
```

---

### Task 4: 过滤器 filters.py

**Files:**
- Create: `src/tgdl/filters.py`
- Test: `tests/unit/test_filters.py`

**Step 1: 写失败测试**

```python
from datetime import datetime, timezone

import pytest

from tgdl.filters import FilterError, MediaFilter, build_filter, compile_regex, parse_datetime, validate_spec
from tgdl.models import ChannelRef, MediaItem, MediaKind, TaskSpec

UTC = timezone.utc


def _spec(**kwargs: object) -> TaskSpec:
    return TaskSpec(link=ChannelRef(username="c"), raw_link="https://t.me/c", **kwargs)  # type: ignore[arg-type]


def _item(kind: MediaKind = MediaKind.VIDEO, name: str = "a.mp4", caption: str = "") -> MediaItem:
    return MediaItem(message_id=1, date=datetime(2026, 1, 1, tzinfo=UTC), kind=kind,
                     file_name=name, size=1, caption=caption)


def test_parse_date_only_is_utc_aware() -> None:
    value = parse_datetime("2026-03-01")
    assert value.tzinfo is not None
    assert value.utcoffset().total_seconds() == 0


def test_parse_date_only_end_of_day() -> None:
    start = parse_datetime("2026-03-01")
    end = parse_datetime("2026-03-01", end_of_day=True)
    assert (end - start).total_seconds() > 86399


def test_parse_datetime_with_time() -> None:
    value = parse_datetime("2026-03-01T12:30")
    assert value.tzinfo is not None


def test_parse_datetime_invalid() -> None:
    with pytest.raises(FilterError, match="日期格式无效"):
        parse_datetime("2026/03/01")


def test_compile_regex_invalid() -> None:
    with pytest.raises(FilterError, match="正则表达式无效"):
        compile_regex("(")


def test_validate_date_and_ids_conflict() -> None:
    with pytest.raises(FilterError, match="不能同时使用"):
        validate_spec(_spec(date_from=datetime(2026, 1, 1, tzinfo=UTC), id_from=1, id_to=2))


def test_validate_reversed_ids() -> None:
    with pytest.raises(FilterError, match="起始序号"):
        validate_spec(_spec(id_from=10, id_to=2))


def test_validate_reversed_dates() -> None:
    with pytest.raises(FilterError, match="不能晚于"):
        validate_spec(_spec(date_from=datetime(2026, 2, 1, tzinfo=UTC), date_to=datetime(2026, 1, 1, tzinfo=UTC)))


def test_validate_ok_passes() -> None:
    validate_spec(_spec(id_from=1, id_to=5, regex="abc"))


def test_filter_matches_caption_or_filename_case_insensitive() -> None:
    flt = MediaFilter(pattern=compile_regex("4k"))
    assert flt.matches(_item(caption="Movie 4K HDR"))
    assert flt.matches(_item(name="movie.4K.mp4"))
    assert not flt.matches(_item(caption="720p", name="x.mp4"))


def test_filter_by_kind() -> None:
    flt = MediaFilter(kind=MediaKind.PHOTO)
    assert flt.matches(_item(kind=MediaKind.PHOTO))
    assert not flt.matches(_item(kind=MediaKind.VIDEO))


def test_build_filter_from_spec() -> None:
    flt = build_filter(_spec(regex="ep\\d+", kind=MediaKind.VIDEO))
    assert flt.matches(_item(caption="EP01"))
    assert not flt.matches(_item(kind=MediaKind.PHOTO, caption="EP01"))
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_filters.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 最小实现**

`src/tgdl/filters.py`:
```python
"""过滤器：正则、时间范围、序号范围，以及 TaskSpec 的互斥校验。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timezone

from tgdl.models import MediaItem, MediaKind, TaskSpec


class FilterError(ValueError):
    """过滤参数不合法。"""


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_datetime(text: str, *, end_of_day: bool = False) -> datetime:
    """解析 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM。无时区按本地时区理解，返回 UTC aware 时间。"""
    cleaned = text.strip()
    try:
        value = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise FilterError(f"日期格式无效: {text}，应为 2026-01-01 或 2026-01-01T12:00") from exc
    if end_of_day and _DATE_ONLY.match(cleaned):
        value = datetime.combine(value.date(), time.max)
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc)


def compile_regex(text: str) -> re.Pattern[str]:
    try:
        return re.compile(text, re.IGNORECASE)
    except re.error as exc:
        raise FilterError(f"正则表达式无效: {text} ({exc})") from exc


def validate_spec(spec: TaskSpec) -> None:
    has_date = spec.date_from is not None or spec.date_to is not None
    has_ids = spec.id_from is not None or spec.id_to is not None
    if has_date and has_ids:
        raise FilterError("--from/--to 与 --ids 不能同时使用")
    if spec.date_from and spec.date_to and spec.date_from > spec.date_to:
        raise FilterError("--from 不能晚于 --to")
    if spec.id_from is not None and spec.id_to is not None and spec.id_from > spec.id_to:
        raise FilterError("--ids 起始序号不能大于结束序号")
    if spec.regex is not None:
        compile_regex(spec.regex)


@dataclass(frozen=True)
class MediaFilter:
    kind: MediaKind = MediaKind.ALL
    pattern: re.Pattern[str] | None = None

    def matches(self, item: MediaItem) -> bool:
        if self.kind is not MediaKind.ALL and item.kind is not self.kind:
            return False
        if self.pattern is None:
            return True
        return bool(self.pattern.search(item.caption) or self.pattern.search(item.file_name))


def build_filter(spec: TaskSpec) -> MediaFilter:
    pattern = compile_regex(spec.regex) if spec.regex else None
    return MediaFilter(kind=spec.kind, pattern=pattern)
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/unit/test_filters.py -q`
Expected: `12 passed`

**Step 5: 提交**

```bash
git add src/tgdl/filters.py tests/unit/test_filters.py
git commit -m "feat: 添加正则、时间、序号过滤器"
```

---

### Task 5: 命令解析 bot/commands.py

**Files:**
- Create: `src/tgdl/bot/commands.py`
- Test: `tests/unit/test_commands.py`

**Step 1: 写失败测试**

```python
import pytest

from tgdl.bot.commands import CommandError, parse_cancel, parse_dl, split_command
from tgdl.models import MediaKind


def test_split_command_strips_bot_suffix() -> None:
    assert split_command("/dl@my_bot https://t.me/x --regex a") == ("/dl", ["https://t.me/x", "--regex", "a"])


def test_split_command_handles_quotes() -> None:
    assert split_command('/dl https://t.me/x --regex "a b"') == ("/dl", ["https://t.me/x", "--regex", "a b"])


def test_split_command_rejects_non_command() -> None:
    with pytest.raises(CommandError):
        split_command("hello")


def test_parse_dl_minimal() -> None:
    spec = parse_dl(["https://t.me/chan"])
    assert spec.link.username == "chan"
    assert spec.kind is MediaKind.ALL
    assert spec.regex is None


def test_parse_dl_full_date_range() -> None:
    spec = parse_dl(["https://t.me/chan", "--regex", "4k", "--from", "2026-01-01", "--to", "2026-02-01", "--type", "video"])
    assert spec.regex == "4k"
    assert spec.date_from is not None and spec.date_to is not None
    assert spec.date_from < spec.date_to
    assert spec.kind is MediaKind.VIDEO


def test_parse_dl_ids() -> None:
    spec = parse_dl(["https://t.me/chan", "--ids", "100-500"])
    assert (spec.id_from, spec.id_to) == (100, 500)


def test_parse_dl_bad_ids_format() -> None:
    with pytest.raises(CommandError, match="--ids 格式"):
        parse_dl(["https://t.me/chan", "--ids", "abc"])


def test_parse_dl_conflicting_ranges() -> None:
    with pytest.raises(CommandError, match="不能同时使用"):
        parse_dl(["https://t.me/chan", "--ids", "1-2", "--from", "2026-01-01"])


def test_parse_dl_bad_link() -> None:
    with pytest.raises(CommandError, match="无法识别的链接"):
        parse_dl(["not-a-link"])


def test_parse_dl_unknown_option() -> None:
    with pytest.raises(CommandError):
        parse_dl(["https://t.me/chan", "--bogus"])


def test_parse_dl_missing_link() -> None:
    with pytest.raises(CommandError):
        parse_dl([])


def test_parse_cancel() -> None:
    assert parse_cancel(["#3"]) == 3
    assert parse_cancel(["3"]) == 3
    with pytest.raises(CommandError, match="用法"):
        parse_cancel([])
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_commands.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 最小实现**

`src/tgdl/bot/commands.py`:
```python
"""Bot 命令文本解析：把 /dl ... 转成 TaskSpec，不涉及网络。"""
from __future__ import annotations

import argparse
import re
import shlex

from tgdl.filters import FilterError, parse_datetime, validate_spec
from tgdl.link_parser import LinkParseError, parse_link
from tgdl.models import MediaKind, TaskSpec

HELP_TEXT = """📖 用法

/dl <链接> [选项]
  --regex <表达式>   按正则过滤（匹配消息文字或文件名，忽略大小写）
  --from <日期>      起始时间，如 2026-01-01 或 2026-01-01T12:00
  --to <日期>        结束时间（含当天）
  --ids <起始>-<结束> 消息序号范围，如 --ids 100-500（与 --from/--to 互斥）
  --type video|photo|all  媒体类型，默认 all

/tasks            查看排队中和进行中的任务
/status           当前任务实时进度
/cancel <任务ID>   取消任务
/help             显示本帮助

示例：
/dl https://t.me/somechannel --regex "4K" --from 2026-01-01 --to 2026-03-01
/dl https://t.me/c/1234567890/50 --ids 50-200 --type video"""


class CommandError(ValueError):
    """命令格式或参数错误，消息可直接回复给用户。"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise CommandError(message)


def _build_dl_parser() -> _Parser:
    parser = _Parser(prog="/dl", add_help=False, exit_on_error=False)
    parser.add_argument("link")
    parser.add_argument("--regex")
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    parser.add_argument("--ids")
    parser.add_argument("--type", dest="kind", choices=[k.value for k in MediaKind], default=MediaKind.ALL.value)
    return parser


_DL_PARSER = _build_dl_parser()
_IDS = re.compile(r"^(\d+)-(\d+)$")


def split_command(text: str) -> tuple[str, list[str]]:
    """返回 (命令名, 参数列表)。命令名统一小写并去掉 @botname 后缀。"""
    try:
        tokens = shlex.split(text.strip())
    except ValueError as exc:
        raise CommandError(f"参数引号不匹配: {exc}") from exc
    if not tokens or not tokens[0].startswith("/"):
        raise CommandError("不是有效命令")
    name = tokens[0].split("@", 1)[0].lower()
    return name, tokens[1:]


def parse_ids(text: str | None) -> tuple[int | None, int | None]:
    if text is None:
        return None, None
    match = _IDS.match(text.strip())
    if not match:
        raise CommandError("--ids 格式应为 起始-结束，例如 --ids 100-500")
    return int(match.group(1)), int(match.group(2))


def parse_dl(args: list[str]) -> TaskSpec:
    ns = _DL_PARSER.parse_args(args)
    try:
        id_from, id_to = parse_ids(ns.ids)
        spec = TaskSpec(
            link=parse_link(ns.link),
            raw_link=ns.link,
            regex=ns.regex,
            date_from=parse_datetime(ns.date_from) if ns.date_from else None,
            date_to=parse_datetime(ns.date_to, end_of_day=True) if ns.date_to else None,
            id_from=id_from,
            id_to=id_to,
            kind=MediaKind(ns.kind),
        )
        validate_spec(spec)
    except (LinkParseError, FilterError) as exc:
        raise CommandError(str(exc)) from exc
    return spec


def parse_cancel(args: list[str]) -> int:
    if len(args) != 1 or not args[0].lstrip("#").isdigit():
        raise CommandError("用法: /cancel <任务ID>")
    return int(args[0].lstrip("#"))
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/unit/test_commands.py -q`
Expected: `12 passed`

**Step 5: 提交**

```bash
git add src/tgdl/bot/commands.py tests/unit/test_commands.py
git commit -m "feat: 添加 Bot 命令解析"
```

---

### Task 6: 路径工具 paths.py

**Files:**
- Create: `src/tgdl/paths.py`
- Test: `tests/unit/test_paths.py`

**Step 1: 写失败测试**

```python
from datetime import datetime, timezone
from pathlib import Path

from tgdl.models import MediaItem, MediaKind
from tgdl.paths import channel_dir_name, cleanup_parts, part_path, sanitize_filename, target_path


class _Entity:
    def __init__(self, id: int, title: str | None = None, username: str | None = None) -> None:
        self.id, self.title, self.username = id, title, username


def test_sanitize_replaces_invalid_chars() -> None:
    assert sanitize_filename('a/b:c*d?e"f<g>h|i.mp4') == "a_b_c_d_e_f_g_h_i.mp4"


def test_sanitize_strips_dots_and_spaces() -> None:
    assert sanitize_filename("  ..name.. ") == "name"


def test_sanitize_empty_becomes_file() -> None:
    assert sanitize_filename("///") == "file"


def test_sanitize_truncates_long_name_keeping_ext() -> None:
    name = sanitize_filename("x" * 300 + ".mp4")
    assert name.endswith(".mp4")
    assert len(name) <= 120


def test_channel_dir_name_prefers_username_then_title_then_id() -> None:
    assert channel_dir_name(_Entity(1, "标题", "user_name")) == "user_name"
    assert channel_dir_name(_Entity(1, "My Chan/2")) == "My Chan_2"
    assert channel_dir_name(_Entity(99)) == "99"


def test_target_path_layout() -> None:
    item = MediaItem(message_id=42, date=datetime(2026, 3, 5, tzinfo=timezone.utc),
                     kind=MediaKind.VIDEO, file_name="v.mp4", size=1)
    assert target_path(Path("dl"), "chan", item) == Path("dl/chan/2026-03/42_v.mp4")


def test_part_path() -> None:
    assert part_path(Path("a/b.mp4")) == Path("a/b.mp4.part")


def test_cleanup_parts_removes_only_part_files(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.mp4.part").write_bytes(b"1")
    (tmp_path / "y.jpg").write_bytes(b"1")
    assert cleanup_parts(tmp_path) == 1
    assert not (tmp_path / "sub" / "x.mp4.part").exists()
    assert (tmp_path / "y.jpg").exists()


def test_cleanup_parts_missing_root(tmp_path: Path) -> None:
    assert cleanup_parts(tmp_path / "nope") == 0
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_paths.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 最小实现**

`src/tgdl/paths.py`:
```python
"""下载路径与文件名处理。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tgdl.models import MediaItem

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_NAME_LENGTH = 120
PART_SUFFIX = ".part"
DEFAULT_NAME = "file"


def sanitize_filename(name: str) -> str:
    cleaned = _INVALID_CHARS.sub("_", name).strip(" .")
    if not cleaned:
        return DEFAULT_NAME
    if len(cleaned) <= MAX_NAME_LENGTH:
        return cleaned
    stem, dot, ext = cleaned.rpartition(".")
    if not dot or len(ext) > 10:
        return cleaned[:MAX_NAME_LENGTH]
    return f"{stem[: MAX_NAME_LENGTH - len(ext) - 1]}.{ext}"


def channel_dir_name(entity: Any) -> str:
    username = getattr(entity, "username", None)
    title = getattr(entity, "title", None)
    return sanitize_filename(username or title or str(entity.id))


def target_path(root: Path, channel_dir: str, item: MediaItem) -> Path:
    return root / channel_dir / item.date.strftime("%Y-%m") / f"{item.message_id}_{item.file_name}"


def part_path(path: Path) -> Path:
    return path.with_name(path.name + PART_SUFFIX)


def cleanup_parts(root: Path) -> int:
    """删除残留的 .part 文件，返回删除数量。"""
    if not root.exists():
        return 0
    parts = tuple(root.rglob(f"*{PART_SUFFIX}"))
    for part in parts:
        part.unlink(missing_ok=True)
    return len(parts)
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/unit/test_paths.py -q`
Expected: `9 passed`

**Step 5: 提交**

```bash
git add src/tgdl/paths.py tests/unit/test_paths.py
git commit -m "feat: 添加下载路径与文件名工具"
```

---

### Task 7: 进度聚合 progress.py

**Files:**
- Create: `src/tgdl/progress.py`
- Test: `tests/unit/test_progress.py`

**Step 1: 写失败测试**

```python
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from tgdl.models import ChannelRef, FileResult, FileStatus, MediaItem, MediaKind, TaskSpec, TaskState, TaskStatus
from tgdl.progress import (
    ProgressTracker, SpeedWindow, eta_seconds, format_bytes, format_duration,
    render_bar, render_progress, render_summary,
)


def _item(mid: int, size: int = 100, name: str = "f.mp4") -> MediaItem:
    return MediaItem(message_id=mid, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     kind=MediaKind.VIDEO, file_name=name, size=size)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_speed_window_needs_two_samples() -> None:
    assert SpeedWindow().add(0.0, 0).speed() == 0.0


def test_speed_window_computes_average() -> None:
    window = SpeedWindow().add(0.0, 0).add(2.0, 200)
    assert window.speed() == 100.0


def test_speed_window_drops_old_samples() -> None:
    window = SpeedWindow(span=10.0).add(0.0, 0).add(5.0, 50).add(20.0, 200)
    assert window.samples[0] == (20.0, 200)


def test_eta() -> None:
    assert eta_seconds(1000, 100.0) == 10.0
    assert eta_seconds(1000, 0.0) is None
    assert eta_seconds(0, 5.0) is None


def test_format_bytes() -> None:
    assert format_bytes(512) == "512 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(3 * 1024**3) == "3.0 GB"


def test_format_duration() -> None:
    assert format_duration(None) == "未知"
    assert format_duration(45) == "45 秒"
    assert format_duration(420) == "约 7 分钟"
    assert format_duration(3900) == "约 1 小时 5 分钟"


def test_render_bar() -> None:
    assert render_bar(0.0) == "░" * 15
    assert render_bar(1.0) == "▓" * 15
    assert render_bar(0.5).count("▓") == 8


def test_tracker_progress_and_done() -> None:
    clock = _Clock()
    tracker = ProgressTracker(task_id=1, channel_title="@c", items=(_item(1), _item(2)), clock=clock)
    assert tracker.snapshot.total_bytes == 200

    tracker.on_file_progress(1, "f.mp4", 50, 100)
    clock.now = 1.0
    tracker.on_file_progress(1, "f.mp4", 100, 100)
    assert tracker.snapshot.done_bytes == 100
    assert tracker.snapshot.speed == 50.0

    tracker.on_file_done(FileResult(item=_item(1), path=Path("x"), status=FileStatus.DONE))
    snap = tracker.snapshot
    assert snap.done == 1 and snap.active == ()
    assert snap.finished_bytes == 100

    tracker.on_file_done(FileResult(item=_item(2), path=Path("y"), status=FileStatus.SKIPPED))
    assert tracker.snapshot.skipped == 1
    assert tracker.snapshot.fraction == 1.0


def test_tracker_failed_does_not_count_bytes() -> None:
    tracker = ProgressTracker(task_id=1, channel_title="@c", items=(_item(1),))
    tracker.on_file_done(FileResult(item=_item(1), path=Path("x"), status=FileStatus.FAILED, error="boom"))
    assert tracker.snapshot.failed == 1
    assert tracker.snapshot.finished_bytes == 0


def test_render_progress_contains_key_fields() -> None:
    tracker = ProgressTracker(task_id=3, channel_title="@chan", items=(_item(1, name="a.mp4"),))
    tracker.on_file_progress(1, "a.mp4", 78, 100)
    tracker.on_flood_wait(12)
    text = render_progress(tracker.snapshot)
    assert "任务 #3" in text and "@chan" in text
    assert "0/1 个文件" in text
    assert "a.mp4  78%" in text
    assert "限流等待 12 秒" in text


def test_render_summary_lists_failures() -> None:
    spec = TaskSpec(link=ChannelRef(username="c"), raw_link="x")
    results = (
        FileResult(item=_item(1), path=Path("a"), status=FileStatus.DONE),
        FileResult(item=_item(2, name="bad.mp4"), path=Path("b"), status=FileStatus.FAILED, error="timeout"),
    )
    state = TaskState(task_id=1, spec=spec, status=TaskStatus.DONE, channel_title="@c", items=(_item(1), _item(2)), results=results)
    text = render_summary(state)
    assert "成功：1" in text and "失败：1" in text
    assert "bad.mp4" in text and "timeout" in text
    assert "已取消" in render_summary(replace(state, status=TaskStatus.CANCELLED))
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_progress.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 最小实现**

`src/tgdl/progress.py`:
```python
"""进度聚合：滑动窗口速度、ETA、文本渲染。快照不可变，Tracker 只替换引用。"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable

from tgdl.models import FileResult, FileStatus, MediaItem, TaskState, TaskStatus

BAR_WIDTH = 15
SPEED_WINDOW_SECONDS = 10.0
MAX_ACTIVE_LINES = 5
MAX_FAILED_LINES = 10

STATUS_LABEL = {
    TaskStatus.QUEUED: "排队中",
    TaskStatus.SCANNING: "扫描中",
    TaskStatus.DOWNLOADING: "下载中",
    TaskStatus.DONE: "已完成",
    TaskStatus.FAILED: "失败",
    TaskStatus.CANCELLED: "已取消",
}


@dataclass(frozen=True)
class SpeedWindow:
    span: float = SPEED_WINDOW_SECONDS
    samples: tuple[tuple[float, int], ...] = ()

    def add(self, now: float, total_bytes: int) -> SpeedWindow:
        kept = tuple(s for s in self.samples if now - s[0] <= self.span)
        return replace(self, samples=kept + ((now, total_bytes),))

    def speed(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self.samples[0], self.samples[-1]
        return (b1 - b0) / (t1 - t0) if t1 > t0 else 0.0


def eta_seconds(remaining_bytes: int, speed: float) -> float | None:
    if speed <= 0 or remaining_bytes <= 0:
        return None
    return remaining_bytes / speed


def format_bytes(n: float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "未知"
    total = int(seconds)
    if total < 60:
        return f"{total} 秒"
    if total < 3600:
        return f"约 {total // 60} 分钟"
    return f"约 {total // 3600} 小时 {total % 3600 // 60} 分钟"


def render_bar(fraction: float, width: int = BAR_WIDTH) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "▓" * filled + "░" * (width - filled)


@dataclass(frozen=True)
class FileProgress:
    message_id: int
    name: str
    current: int
    total: int


@dataclass(frozen=True)
class ProgressSnapshot:
    task_id: int
    channel_title: str
    status: TaskStatus
    total_files: int
    total_bytes: int
    done: int = 0
    skipped: int = 0
    failed: int = 0
    finished_bytes: int = 0
    active: tuple[FileProgress, ...] = ()
    speed: float = 0.0
    flood_wait: int | None = None

    @property
    def done_bytes(self) -> int:
        return self.finished_bytes + sum(f.current for f in self.active)

    @property
    def finished_files(self) -> int:
        return self.done + self.skipped + self.failed

    @property
    def fraction(self) -> float:
        return self.done_bytes / self.total_bytes if self.total_bytes else 0.0


_COUNTER_FIELD = {FileStatus.DONE: "done", FileStatus.SKIPPED: "skipped", FileStatus.FAILED: "failed"}


class ProgressTracker:
    """持有最新的不可变快照；每次更新生成新快照替换引用。"""

    def __init__(self, task_id: int, channel_title: str, items: tuple[MediaItem, ...],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._window = SpeedWindow()
        self._snap = ProgressSnapshot(
            task_id=task_id, channel_title=channel_title, status=TaskStatus.DOWNLOADING,
            total_files=len(items), total_bytes=sum(i.size for i in items),
        )

    @property
    def snapshot(self) -> ProgressSnapshot:
        return self._snap

    def on_file_progress(self, message_id: int, name: str, current: int, total: int) -> None:
        others = tuple(f for f in self._snap.active if f.message_id != message_id)
        entry = FileProgress(message_id=message_id, name=name, current=current, total=total)
        self._snap = replace(self._snap, active=others + (entry,), flood_wait=None)
        self._tick()

    def on_file_done(self, result: FileResult) -> None:
        others = tuple(f for f in self._snap.active if f.message_id != result.item.message_id)
        field = _COUNTER_FIELD[result.status]
        gained = result.item.size if result.status is not FileStatus.FAILED else 0
        self._snap = replace(
            self._snap, active=others, finished_bytes=self._snap.finished_bytes + gained,
            **{field: getattr(self._snap, field) + 1},
        )
        self._tick()

    def on_flood_wait(self, seconds: int) -> None:
        self._snap = replace(self._snap, flood_wait=seconds)

    def _tick(self) -> None:
        self._window = self._window.add(self._clock(), self._snap.done_bytes)
        self._snap = replace(self._snap, speed=self._window.speed())


def render_progress(snap: ProgressSnapshot) -> str:
    remaining = snap.total_bytes - snap.done_bytes
    lines = [
        f"📥 任务 #{snap.task_id}  {snap.channel_title}",
        f"状态：{STATUS_LABEL[snap.status]}  {snap.finished_files}/{snap.total_files} 个文件",
        f"进度：{render_bar(snap.fraction)} {snap.fraction * 100:.1f}%  "
        f"({format_bytes(snap.done_bytes)} / {format_bytes(snap.total_bytes)})",
        f"速度：{format_bytes(snap.speed)}/s    剩余：{format_duration(eta_seconds(remaining, snap.speed))}",
    ]
    if snap.flood_wait:
        lines.append(f"⚠️ 限流等待 {snap.flood_wait} 秒")
    if snap.active:
        lines.append("正在下载：")
        for entry in snap.active[:MAX_ACTIVE_LINES]:
            pct = entry.current / entry.total * 100 if entry.total else 0.0
            lines.append(f"  • {entry.name}  {pct:.0f}%")
    lines.append(f"已跳过：{snap.skipped} 个（已存在）  失败：{snap.failed} 个")
    return "\n".join(lines)


def render_summary(state: TaskState) -> str:
    counts = {status: sum(1 for r in state.results if r.status is status) for status in FileStatus}
    total_bytes = sum(r.item.size for r in state.results if r.status is not FileStatus.FAILED)
    icon = {TaskStatus.DONE: "✅", TaskStatus.CANCELLED: "🚫", TaskStatus.FAILED: "❌"}.get(state.status, "ℹ️")
    lines = [
        f"{icon} 任务 #{state.task_id}  {state.channel_title}  {STATUS_LABEL[state.status]}",
        f"成功：{counts[FileStatus.DONE]}  跳过：{counts[FileStatus.SKIPPED]}  失败：{counts[FileStatus.FAILED]}"
        f"  共 {len(state.items)} 个，{format_bytes(total_bytes)}",
    ]
    failures = tuple(r for r in state.results if r.status is FileStatus.FAILED)
    if failures:
        lines.append("失败列表：")
        lines.extend(f"  • {r.item.file_name}: {r.error}" for r in failures[:MAX_FAILED_LINES])
        if len(failures) > MAX_FAILED_LINES:
            lines.append(f"  …另有 {len(failures) - MAX_FAILED_LINES} 个，详见日志")
    if state.error:
        lines.append(f"错误：{state.error}")
    return "\n".join(lines)
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/unit/test_progress.py -q`
Expected: `12 passed`

**Step 5: 提交**

```bash
git add src/tgdl/progress.py tests/unit/test_progress.py
git commit -m "feat: 添加进度聚合、速度窗口与文本渲染"
```

---

### Task 8: 节流进度上报 reporter.py

**Files:**
- Create: `src/tgdl/reporter.py`
- Test: `tests/unit/test_reporter.py`

**Step 1: 写失败测试**

```python
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
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_reporter.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 最小实现**

`src/tgdl/reporter.py`:
```python
"""按固定间隔把进度文本写回同一条 Bot 消息，去重并容忍编辑失败。"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from telethon.errors import MessageNotModifiedError, RPCError

log = logging.getLogger(__name__)

DEFAULT_INTERVAL = 5.0

EditFn = Callable[[str], Awaitable[None]]


class ProgressReporter:
    def __init__(self, edit: EditFn, interval: float = DEFAULT_INTERVAL) -> None:
        self._edit = edit
        self._interval = interval
        self._last_text = ""

    async def update(self, text: str) -> bool:
        """文本变化时才编辑；返回是否真正发送了编辑。"""
        if text == self._last_text:
            return False
        try:
            await self._edit(text)
        except MessageNotModifiedError:
            return False
        except RPCError as exc:
            log.warning("编辑进度消息失败: %s", exc)
            return False
        self._last_text = text
        return True

    async def run(self, render: Callable[[], str], stop: asyncio.Event) -> None:
        """循环刷新直到 stop 被置位，最后强制刷新一次。"""
        while not stop.is_set():
            await self.update(render())
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue
        await self.update(render())
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/unit/test_reporter.py -q`
Expected: `3 passed`

**Step 5: 提交**

```bash
git add src/tgdl/reporter.py tests/unit/test_reporter.py
git commit -m "feat: 添加节流进度上报器"
```

---

### Task 9: 测试替身与扫描器 scanner.py

**Files:**
- Create: `tests/fakes/telegram.py`
- Create: `src/tgdl/scanner.py`
- Test: `tests/integration/test_scanner.py`

**Step 1: 写测试替身（所有后续集成测试共用）**

`tests/fakes/telegram.py`:
```python
"""Telethon 客户端与消息的测试替身。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator


@dataclass(frozen=True)
class FakeFile:
    name: str | None = None
    size: int = 0
    mime_type: str | None = None
    ext: str | None = None


@dataclass(frozen=True)
class FakeMessage:
    id: int
    date: datetime
    message: str = ""
    file: FakeFile | None = None
    sticker: object | None = None
    grouped_id: int | None = None


@dataclass(frozen=True)
class FakeEntity:
    id: int
    title: str | None = None
    username: str | None = None


@dataclass
class FakeClient:
    """模拟 iter_messages / get_messages / download_media / get_entity。"""

    messages: tuple[FakeMessage, ...] = ()
    entity: FakeEntity | None = None
    entity_error: Exception | None = None
    failures: list[Exception] = field(default_factory=list)  # 依次抛出，用完后正常下载
    chunk: int = 4
    delay: float = 0.0
    download_calls: list[int] = field(default_factory=list)
    max_concurrent: int = 0
    _in_flight: int = 0

    def __post_init__(self) -> None:
        self.messages = tuple(sorted(self.messages, key=lambda m: m.id))

    async def get_entity(self, ref: Any) -> FakeEntity | None:
        if self.entity_error:
            raise self.entity_error
        return self.entity

    async def iter_messages(self, entity: Any, reverse: bool = False, min_id: int = 0,
                            max_id: int = 0, offset_date: datetime | None = None) -> AsyncIterator[FakeMessage]:
        selected = [
            m for m in self.messages
            if (min_id == 0 or m.id > min_id)
            and (max_id == 0 or m.id < max_id)
            and (offset_date is None or m.date > offset_date)
        ]
        for message in (selected if reverse else reversed(selected)):
            yield message

    async def get_messages(self, entity: Any, ids: int) -> FakeMessage | None:
        return next((m for m in self.messages if m.id == ids), None)

    async def download_media(self, message: FakeMessage, file: str, progress_callback: Any = None) -> str:
        self.download_calls.append(message.id)
        self._in_flight += 1
        self.max_concurrent = max(self.max_concurrent, self._in_flight)
        try:
            if self.failures:
                raise self.failures.pop(0)
            assert message.file is not None
            data = b"x" * message.file.size
            with open(file, "wb") as handle:
                for start in range(0, len(data), self.chunk):
                    await asyncio.sleep(self.delay)
                    handle.write(data[start:start + self.chunk])
                    if progress_callback:
                        progress_callback(min(start + self.chunk, len(data)), len(data))
            return file
        finally:
            self._in_flight -= 1


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edits: list[tuple[int, str]] = []

    async def send(self, text: str) -> int:
        self.sent.append(text)
        return len(self.sent)

    async def edit(self, message_id: int, text: str) -> None:
        self.edits.append((message_id, text))
```

**Step 2: 写失败测试**

`tests/integration/test_scanner.py`:
```python
from datetime import datetime, timedelta, timezone

import pytest
from telethon.errors import ChannelPrivateError, UsernameNotOccupiedError

from tgdl.filters import MediaFilter, compile_regex
from tgdl.models import ChannelRef, MediaKind, TaskSpec
from tgdl.scanner import ChannelAccessError, extract_media, iter_kwargs, resolve_channel, scan
from tests.fakes.telegram import FakeClient, FakeEntity, FakeFile, FakeMessage

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _msg(mid: int, *, days: int = 0, text: str = "", file: FakeFile | None = None, **kw: object) -> FakeMessage:
    return FakeMessage(id=mid, date=T0 + timedelta(days=days), message=text, file=file, **kw)  # type: ignore[arg-type]


VIDEO = FakeFile(name="clip.mp4", size=100, mime_type="video/mp4", ext=".mp4")
PHOTO = FakeFile(name=None, size=20, mime_type="image/jpeg", ext=".jpg")
IMG_DOC = FakeFile(name="raw.png", size=30, mime_type="image/png", ext=".png")
PDF = FakeFile(name="doc.pdf", size=5, mime_type="application/pdf", ext=".pdf")


def _spec(**kw: object) -> TaskSpec:
    return TaskSpec(link=ChannelRef(username="c"), raw_link="x", **kw)  # type: ignore[arg-type]


def test_extract_video() -> None:
    item = extract_media(_msg(1, text="hi", file=VIDEO))
    assert item is not None
    assert (item.kind, item.file_name, item.size, item.caption) == (MediaKind.VIDEO, "clip.mp4", 100, "hi")


def test_extract_photo_without_name_gets_generated_name() -> None:
    item = extract_media(_msg(2, file=PHOTO))
    assert item is not None
    assert item.kind is MediaKind.PHOTO and item.file_name == "photo.jpg"


def test_extract_image_document() -> None:
    item = extract_media(_msg(3, file=IMG_DOC))
    assert item is not None and item.kind is MediaKind.PHOTO


def test_extract_ignores_sticker_pdf_and_text() -> None:
    assert extract_media(_msg(4, file=FakeFile(mime_type="image/webp"), sticker=object())) is None
    assert extract_media(_msg(5, file=PDF)) is None
    assert extract_media(_msg(6, text="plain")) is None


def test_iter_kwargs_ids_range_is_exclusive_bounds() -> None:
    assert iter_kwargs(_spec(id_from=100, id_to=500)) == {"reverse": True, "min_id": 99, "max_id": 501}


def test_iter_kwargs_date_and_start_message() -> None:
    spec = TaskSpec(link=ChannelRef(username="c", message_id=10), raw_link="x", date_from=T0)
    assert iter_kwargs(spec) == {"reverse": True, "offset_date": T0, "min_id": 9}


async def test_scan_applies_date_to_and_regex() -> None:
    client = FakeClient(messages=(
        _msg(1, days=0, text="EP01", file=VIDEO),
        _msg(2, days=1, text="EP02", file=VIDEO),
        _msg(3, days=5, text="EP03", file=VIDEO),
    ))
    spec = _spec(date_from=T0 - timedelta(days=1), date_to=T0 + timedelta(days=2))
    items = await scan(client, object(), spec, MediaFilter(pattern=compile_regex("ep0[12]")))
    assert [i.message_id for i in items] == [1, 2]


async def test_scan_ids_range() -> None:
    client = FakeClient(messages=tuple(_msg(i, file=VIDEO) for i in range(1, 11)))
    items = await scan(client, object(), _spec(id_from=3, id_to=5), MediaFilter())
    assert [i.message_id for i in items] == [3, 4, 5]


async def test_scan_propagates_album_caption() -> None:
    client = FakeClient(messages=(
        _msg(1, text="Album 4K", file=PHOTO, grouped_id=77),
        _msg(2, text="", file=PHOTO, grouped_id=77),
        _msg(3, text="", file=PHOTO),
    ))
    items = await scan(client, object(), _spec(), MediaFilter(pattern=compile_regex("4k")))
    assert [i.message_id for i in items] == [1, 2]
    assert items[1].caption == "Album 4K"


async def test_resolve_by_username() -> None:
    entity = FakeEntity(id=1, username="c")
    assert await resolve_channel(FakeClient(entity=entity), ChannelRef(username="c")) is entity


@pytest.mark.parametrize(
    ("error", "match"),
    [(UsernameNotOccupiedError(request=None), "不存在"), (ChannelPrivateError(request=None), "无权访问")],
)
async def test_resolve_maps_errors(error: Exception, match: str) -> None:
    with pytest.raises(ChannelAccessError, match=match):
        await resolve_channel(FakeClient(entity_error=error), ChannelRef(username="c"))
```

**Step 3: 运行确认失败**

Run: `uv run pytest tests/integration/test_scanner.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'tgdl.scanner'`

**Step 4: 最小实现**

`src/tgdl/scanner.py`:
```python
"""解析频道实体并扫描消息，产出待下载的 MediaItem 列表。"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from telethon.errors import (
    ChannelPrivateError, InviteHashExpiredError, InviteHashInvalidError,
    UserAlreadyParticipantError, UsernameInvalidError, UsernameNotOccupiedError,
)
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import PeerChannel

from tgdl.filters import MediaFilter
from tgdl.models import ChannelRef, MediaItem, MediaKind, TaskSpec
from tgdl.paths import sanitize_filename

log = logging.getLogger(__name__)


class ChannelAccessError(RuntimeError):
    """频道无法访问，消息可直接回复给用户。"""


async def _join_by_invite(client: Any, invite_hash: str) -> Any:
    try:
        updates = await client(ImportChatInviteRequest(invite_hash))
        return updates.chats[0]
    except UserAlreadyParticipantError:
        return (await client(CheckChatInviteRequest(invite_hash))).chat
    except (InviteHashExpiredError, InviteHashInvalidError) as exc:
        raise ChannelAccessError("邀请链接无效或已过期") from exc


async def resolve_channel(client: Any, ref: ChannelRef) -> Any:
    try:
        if ref.invite_hash:
            return await _join_by_invite(client, ref.invite_hash)
        if ref.channel_id is not None:
            return await client.get_entity(PeerChannel(ref.channel_id))
        return await client.get_entity(ref.username)
    except (UsernameInvalidError, UsernameNotOccupiedError) as exc:
        raise ChannelAccessError("频道不存在") from exc
    except ChannelPrivateError as exc:
        raise ChannelAccessError("频道为私有或已被封禁，当前账号无权访问") from exc
    except ValueError as exc:
        raise ChannelAccessError(f"无法解析频道: {exc}") from exc


def extract_media(message: Any) -> MediaItem | None:
    file = getattr(message, "file", None)
    if file is None or getattr(message, "sticker", None) is not None:
        return None
    mime = (file.mime_type or "").lower()
    if mime.startswith("image/"):
        kind = MediaKind.PHOTO
    elif mime.startswith("video/"):
        kind = MediaKind.VIDEO
    else:
        return None
    name = file.name or f"{kind.value}{file.ext or ''}"
    return MediaItem(
        message_id=message.id, date=message.date, kind=kind,
        file_name=sanitize_filename(name), size=file.size or 0, caption=message.message or "",
    )


def iter_kwargs(spec: TaskSpec) -> dict[str, Any]:
    """把过滤范围下推到 Telethon iter_messages（min_id/max_id 为开区间）。"""
    if spec.id_from is not None:
        max_id = spec.id_to + 1 if spec.id_to is not None else 0
        return {"reverse": True, "min_id": spec.id_from - 1, "max_id": max_id}
    kwargs: dict[str, Any] = {"reverse": True}
    if spec.date_from is not None:
        kwargs["offset_date"] = spec.date_from
    if spec.link.message_id is not None:
        kwargs["min_id"] = spec.link.message_id - 1
    return kwargs


def _propagate_album_captions(pairs: tuple[tuple[Any, MediaItem], ...]) -> tuple[MediaItem, ...]:
    captions = {m.grouped_id: item.caption for m, item in pairs if m.grouped_id and item.caption}
    return tuple(
        replace(item, caption=captions.get(m.grouped_id, item.caption)) if m.grouped_id else item
        for m, item in pairs
    )


async def scan(client: Any, entity: Any, spec: TaskSpec, media_filter: MediaFilter) -> tuple[MediaItem, ...]:
    collected: list[tuple[Any, MediaItem]] = []
    async for message in client.iter_messages(entity, **iter_kwargs(spec)):
        if spec.date_to is not None and message.date > spec.date_to:
            break
        item = extract_media(message)
        if item is not None:
            collected.append((message, item))
    items = _propagate_album_captions(tuple(collected))
    matched = tuple(item for item in items if media_filter.matches(item))
    log.info("扫描完成: 共 %d 个媒体，匹配 %d 个", len(items), len(matched))
    return matched
```

**Step 5: 运行确认通过**

Run: `uv run pytest tests/integration/test_scanner.py -q`
Expected: `13 passed`

**Step 6: 提交**

```bash
git add tests/fakes/telegram.py src/tgdl/scanner.py tests/integration/test_scanner.py
git commit -m "feat: 添加频道解析与消息扫描器"
```

---

### Task 10: 下载器 downloader.py

**Files:**
- Create: `src/tgdl/downloader.py`
- Test: `tests/integration/test_downloader.py`

**Step 1: 写失败测试**

```python
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from telethon.errors import FloodWaitError

from tgdl.downloader import download_all, download_item
from tgdl.models import FileStatus, MediaItem, MediaKind
from tgdl.progress import ProgressTracker
from tests.fakes.telegram import FakeClient, FakeFile, FakeMessage

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _pair(mid: int, size: int = 16) -> tuple[FakeMessage, MediaItem]:
    file = FakeFile(name=f"v{mid}.mp4", size=size, mime_type="video/mp4", ext=".mp4")
    msg = FakeMessage(id=mid, date=T0, file=file)
    item = MediaItem(message_id=mid, date=T0, kind=MediaKind.VIDEO, file_name=file.name or "", size=size)
    return msg, item


async def _noop_sleep(_: float) -> None:
    return None


def _cb() -> tuple[list[tuple[int, int]], list[int]]:
    return [], []


async def test_downloads_and_renames_part(tmp_path: Path) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,))
    progress: list[tuple[int, int]] = []
    result = await download_item(client, object(), item, tmp_path / "a" / "1_v1.mp4",
                                 on_progress=lambda c, t: progress.append((c, t)), on_flood_wait=lambda s: None)
    assert result.status is FileStatus.DONE
    assert (tmp_path / "a" / "1_v1.mp4").stat().st_size == 16
    assert not list(tmp_path.rglob("*.part"))
    assert progress[-1] == (16, 16)


async def test_skips_existing_file_with_same_size(tmp_path: Path) -> None:
    msg, item = _pair(1)
    target = tmp_path / "1_v1.mp4"
    target.write_bytes(b"x" * 16)
    client = FakeClient(messages=(msg,))
    result = await download_item(client, object(), item, target, on_progress=lambda c, t: None, on_flood_wait=lambda s: None)
    assert result.status is FileStatus.SKIPPED
    assert client.download_calls == []


async def test_retries_then_succeeds(tmp_path: Path) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,), failures=[OSError("net"), OSError("net")])
    result = await download_item(client, object(), item, tmp_path / "1.mp4", on_progress=lambda c, t: None,
                                 on_flood_wait=lambda s: None, max_retries=3, sleep=_noop_sleep)
    assert result.status is FileStatus.DONE
    assert len(client.download_calls) == 3


async def test_retries_exhausted_marks_failed_and_removes_part(tmp_path: Path) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,), failures=[OSError("net")] * 5)
    result = await download_item(client, object(), item, tmp_path / "1.mp4", on_progress=lambda c, t: None,
                                 on_flood_wait=lambda s: None, max_retries=2, sleep=_noop_sleep)
    assert result.status is FileStatus.FAILED
    assert result.error is not None and "net" in result.error
    assert len(client.download_calls) == 3
    assert not list(tmp_path.rglob("*.part"))


async def test_flood_wait_reports_and_retries_without_consuming_attempts(tmp_path: Path) -> None:
    msg, item = _pair(1)
    client = FakeClient(messages=(msg,), failures=[FloodWaitError(request=None, capture=7)])
    waits: list[int] = []
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    result = await download_item(client, object(), item, tmp_path / "1.mp4", on_progress=lambda c, t: None,
                                 on_flood_wait=waits.append, max_retries=0, sleep=sleep)
    assert result.status is FileStatus.DONE
    assert waits == [7] and slept and slept[0] >= 7


async def test_missing_message_fails_without_retry(tmp_path: Path) -> None:
    _, item = _pair(99)
    client = FakeClient(messages=())
    result = await download_item(client, object(), item, tmp_path / "99.mp4", on_progress=lambda c, t: None,
                                 on_flood_wait=lambda s: None, max_retries=3, sleep=_noop_sleep)
    assert result.status is FileStatus.FAILED and client.download_calls == []


async def test_cancel_removes_part(tmp_path: Path) -> None:
    msg, item = _pair(1, size=64)
    client = FakeClient(messages=(msg,), delay=0.01)
    task = asyncio.create_task(download_item(client, object(), item, tmp_path / "1.mp4",
                                             on_progress=lambda c, t: None, on_flood_wait=lambda s: None))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert not list(tmp_path.rglob("*"))


async def test_download_all_respects_concurrency_and_updates_tracker(tmp_path: Path) -> None:
    pairs = [_pair(i) for i in range(1, 7)]
    client = FakeClient(messages=tuple(m for m, _ in pairs), delay=0.005)
    items = tuple(i for _, i in pairs)
    tracker = ProgressTracker(task_id=1, channel_title="c", items=items)
    results = await download_all(client, object(), items, tmp_path, "chan", tracker, concurrency=2, max_retries=0)
    assert len(results) == 6 and all(r.status is FileStatus.DONE for r in results)
    assert client.max_concurrent == 2
    assert tracker.snapshot.done == 6 and tracker.snapshot.fraction == 1.0
    assert (tmp_path / "chan" / "2026-01" / "3_v3.mp4").exists()
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/integration/test_downloader.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 最小实现**

`src/tgdl/downloader.py`:
```python
"""单文件下载（跳过/重试/限流/取消清理）与任务级并发下载。"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from telethon.errors import FloodWaitError, RPCError

from tgdl.models import FileResult, FileStatus, MediaItem
from tgdl.paths import part_path, target_path
from tgdl.progress import ProgressTracker

log = logging.getLogger(__name__)

BACKOFF_BASE_SECONDS = 2.0
FLOOD_WAIT_MARGIN_SECONDS = 1

ProgressFn = Callable[[int, int], None]
FloodWaitFn = Callable[[int], None]
SleepFn = Callable[[float], Awaitable[None]]


class MediaUnavailableError(RuntimeError):
    """消息已被删除或无法获取，不重试。"""


def _is_complete(path: Path, item: MediaItem) -> bool:
    return path.exists() and path.stat().st_size == item.size


async def _download_once(client: Any, entity: Any, item: MediaItem, part: Path, on_progress: ProgressFn) -> None:
    message = await client.get_messages(entity, ids=item.message_id)
    if message is None:
        raise MediaUnavailableError(f"消息 {item.message_id} 已不存在")
    await client.download_media(message, file=str(part), progress_callback=on_progress)


async def download_item(
    client: Any, entity: Any, item: MediaItem, path: Path, *,
    on_progress: ProgressFn, on_flood_wait: FloodWaitFn,
    max_retries: int = 3, sleep: SleepFn = asyncio.sleep,
) -> FileResult:
    if _is_complete(path, item):
        return FileResult(item=item, path=path, status=FileStatus.SKIPPED)
    part = part_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        try:
            await _download_once(client, entity, item, part, on_progress)
            part.replace(path)
            return FileResult(item=item, path=path, status=FileStatus.DONE)
        except FloodWaitError as exc:
            log.warning("限流 %d 秒: %s", exc.seconds, item.file_name)
            on_flood_wait(exc.seconds)
            await sleep(exc.seconds + FLOOD_WAIT_MARGIN_SECONDS)
        except asyncio.CancelledError:
            part.unlink(missing_ok=True)
            raise
        except MediaUnavailableError as exc:
            part.unlink(missing_ok=True)
            return FileResult(item=item, path=path, status=FileStatus.FAILED, error=str(exc))
        except (OSError, RPCError) as exc:
            part.unlink(missing_ok=True)
            if attempt >= max_retries:
                log.error("下载失败 %s: %s", item.file_name, exc)
                return FileResult(item=item, path=path, status=FileStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
            attempt += 1
            await sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))


async def download_all(
    client: Any, entity: Any, items: tuple[MediaItem, ...], root: Path, channel_dir: str,
    tracker: ProgressTracker, *, concurrency: int, max_retries: int,
) -> tuple[FileResult, ...]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(item: MediaItem) -> FileResult:
        async with semaphore:
            result = await download_item(
                client, entity, item, target_path(root, channel_dir, item),
                on_progress=lambda cur, total: tracker.on_file_progress(item.message_id, item.file_name, cur, total),
                on_flood_wait=tracker.on_flood_wait, max_retries=max_retries,
            )
            tracker.on_file_done(result)
            return result

    return tuple(await asyncio.gather(*(one(item) for item in items)))
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/integration/test_downloader.py -q`
Expected: `8 passed`

**Step 5: 提交**

```bash
git add src/tgdl/downloader.py tests/integration/test_downloader.py
git commit -m "feat: 添加带重试、限流处理和并发控制的下载器"
```

---

### Task 11: 任务队列 task_queue.py

**Files:**
- Create: `src/tgdl/task_queue.py`
- Test: `tests/integration/test_task_queue.py`

**Step 1: 写失败测试**

```python
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
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/integration/test_task_queue.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 最小实现**

`src/tgdl/task_queue.py`:
```python
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
            self._cancel_requested = True
            self._current.cancel()
            return True
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
            final = await self._current
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
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/integration/test_task_queue.py -q`
Expected: `7 passed`

**Step 5: 提交**

```bash
git add src/tgdl/task_queue.py tests/integration/test_task_queue.py
git commit -m "feat: 添加串行任务队列与取消支持"
```

---

### Task 12: 任务执行器 worker.py 与通知器 bot/notifier.py

**Files:**
- Create: `src/tgdl/bot/notifier.py`
- Create: `src/tgdl/worker.py`
- Test: `tests/integration/test_worker.py`

**Step 1: 写通知器（真实 Telethon 封装，不进单测，覆盖率已排除）**

`src/tgdl/bot/notifier.py`:
```python
"""通过 Bot 客户端向 OWNER 发送与编辑消息。"""
from __future__ import annotations

from typing import Any, Protocol


class Notifier(Protocol):
    async def send(self, text: str) -> int: ...
    async def edit(self, message_id: int, text: str) -> None: ...


class TelegramNotifier:
    def __init__(self, bot_client: Any, owner_id: int) -> None:
        self._bot = bot_client
        self._owner_id = owner_id

    async def send(self, text: str) -> int:
        message = await self._bot.send_message(self._owner_id, text)
        return int(message.id)

    async def edit(self, message_id: int, text: str) -> None:
        await self._bot.edit_message(self._owner_id, message_id, text)
```

**Step 2: 写失败测试**

`tests/integration/test_worker.py`:
```python
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from telethon.errors import ChannelPrivateError

from tgdl.models import ChannelRef, FileStatus, TaskSpec, TaskState, TaskStatus
from tgdl.worker import TaskWorker, WorkerConfig
from tests.fakes.telegram import FakeClient, FakeEntity, FakeFile, FakeMessage, FakeNotifier

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
VIDEO = FakeFile(name="v.mp4", size=8, mime_type="video/mp4", ext=".mp4")


def _client(n: int = 2, **kw: object) -> FakeClient:
    return FakeClient(
        messages=tuple(FakeMessage(id=i, date=T0, message=f"ep{i}", file=VIDEO) for i in range(1, n + 1)),
        entity=FakeEntity(id=1, title="My Chan", username="mychan"), **kw,  # type: ignore[arg-type]
    )


def _worker(client: FakeClient, tmp_path: Path, notifier: FakeNotifier) -> TaskWorker:
    config = WorkerConfig(download_dir=tmp_path, concurrency=2, max_retries=0, progress_interval=0.01)
    return TaskWorker(client, notifier, config)


def _state(**kw: object) -> TaskState:
    return TaskState(task_id=1, spec=TaskSpec(link=ChannelRef(username="mychan"), raw_link="https://t.me/mychan", **kw))  # type: ignore[arg-type]


async def test_happy_path_downloads_and_reports(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    published: list[TaskState] = []
    final = await _worker(_client(3), tmp_path, notifier).run(_state(), published.append)

    assert final.status is TaskStatus.DONE
    assert [r.status for r in final.results] == [FileStatus.DONE] * 3
    assert final.channel_title == "@mychan"
    assert [s.status for s in published] == [TaskStatus.SCANNING, TaskStatus.DOWNLOADING]
    assert (tmp_path / "mychan" / "2026-01" / "2_v.mp4").exists()
    assert any("共 3 个文件" in text for text in notifier.sent)
    assert notifier.edits and "成功：3" in notifier.edits[-1][1]


async def test_no_items_finishes_with_message(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    final = await _worker(_client(2), tmp_path, notifier).run(_state(regex="nomatch"), lambda s: None)
    assert final.status is TaskStatus.DONE and final.results == ()
    assert any("没有匹配" in text for text in notifier.sent)


async def test_channel_access_error_fails_gracefully(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    client = _client(entity_error=ChannelPrivateError(request=None))
    final = await _worker(client, tmp_path, notifier).run(_state(), lambda s: None)
    assert final.status is TaskStatus.FAILED
    assert final.error is not None and "无权访问" in final.error
    assert any("无权访问" in text for text in notifier.sent)


async def test_current_snapshot_available_during_download(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    worker = _worker(_client(2, delay=0.01), tmp_path, notifier)
    assert worker.current_snapshot() is None
    task = asyncio.create_task(worker.run(_state(), lambda s: None))
    await asyncio.sleep(0.02)
    snap = worker.current_snapshot()
    assert snap is not None and snap.total_files == 2
    await task
    assert worker.current_snapshot() is None


async def test_cancel_sends_notice_and_reraises(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    worker = _worker(_client(2, delay=0.05), tmp_path, notifier)
    task = asyncio.create_task(worker.run(_state(), lambda s: None))
    await asyncio.sleep(0.03)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert any("已取消" in text for _, text in notifier.edits) or any("已取消" in t for t in notifier.sent)
    assert not list(tmp_path.rglob("*.part"))
```

**Step 3: 运行确认失败**

Run: `uv run pytest tests/integration/test_worker.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'tgdl.worker'`

**Step 4: 最小实现**

`src/tgdl/worker.py`:
```python
"""单个任务的完整执行流程：解析频道 → 扫描 → 并发下载 → 汇总。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from tgdl.bot.notifier import Notifier
from tgdl.downloader import download_all
from tgdl.filters import FilterError, build_filter
from tgdl.models import TaskState, TaskStatus
from tgdl.paths import channel_dir_name
from tgdl.progress import ProgressSnapshot, ProgressTracker, format_bytes, render_progress, render_summary
from tgdl.reporter import ProgressReporter
from tgdl.scanner import ChannelAccessError, resolve_channel, scan

log = logging.getLogger(__name__)

Publish = Callable[[TaskState], None]


@dataclass(frozen=True)
class WorkerConfig:
    download_dir: Path
    concurrency: int
    max_retries: int
    progress_interval: float


def display_title(entity: Any) -> str:
    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"
    return getattr(entity, "title", None) or str(entity.id)


class TaskWorker:
    def __init__(self, user_client: Any, notifier: Notifier, config: WorkerConfig) -> None:
        self._client = user_client
        self._notifier = notifier
        self._config = config
        self._tracker: ProgressTracker | None = None

    def current_snapshot(self) -> ProgressSnapshot | None:
        return self._tracker.snapshot if self._tracker else None

    async def run(self, state: TaskState, publish: Publish) -> TaskState:
        try:
            return await self._run(state, publish)
        except (ChannelAccessError, FilterError) as exc:
            await self._notifier.send(f"❌ 任务 #{state.task_id} 失败：{exc}")
            return replace(state, status=TaskStatus.FAILED, error=str(exc))
        except asyncio.CancelledError:
            await self._notifier.send(f"🚫 任务 #{state.task_id} 已取消")
            raise
        finally:
            self._tracker = None

    async def _run(self, state: TaskState, publish: Publish) -> TaskState:
        spec = state.spec
        await self._notifier.send(f"🔍 任务 #{state.task_id} 开始扫描 {spec.raw_link}")
        entity = await resolve_channel(self._client, spec.link)
        state = replace(state, status=TaskStatus.SCANNING, channel_title=display_title(entity))
        publish(state)

        items = await scan(self._client, entity, spec, build_filter(spec))
        if not items:
            await self._notifier.send(f"ℹ️ 任务 #{state.task_id} 没有匹配的媒体")
            return replace(state, status=TaskStatus.DONE)
        state = replace(state, status=TaskStatus.DOWNLOADING, items=items)
        publish(state)

        total = format_bytes(sum(i.size for i in items))
        message_id = await self._notifier.send(
            f"📋 任务 #{state.task_id}  {state.channel_title}\n共 {len(items)} 个文件，总大小 {total}，开始下载…"
        )
        results = await self._download_with_progress(state, entity, message_id)
        final = replace(state, status=TaskStatus.DONE, results=results)
        await self._notifier.edit(message_id, render_summary(final))
        return final

    async def _download_with_progress(self, state: TaskState, entity: Any, message_id: int) -> tuple:
        tracker = ProgressTracker(state.task_id, state.channel_title, state.items)
        self._tracker = tracker
        reporter = ProgressReporter(lambda text: self._notifier.edit(message_id, text), self._config.progress_interval)
        stop = asyncio.Event()
        report_task = asyncio.create_task(reporter.run(lambda: render_progress(tracker.snapshot), stop))
        try:
            return await download_all(
                self._client, entity, state.items, self._config.download_dir, channel_dir_name(entity),
                tracker, concurrency=self._config.concurrency, max_retries=self._config.max_retries,
            )
        finally:
            stop.set()
            await report_task
```

**Step 5: 运行确认通过**

Run: `uv run pytest tests/integration/test_worker.py -q`
Expected: `5 passed`

**Step 6: 提交**

```bash
git add src/tgdl/worker.py src/tgdl/bot/notifier.py tests/integration/test_worker.py
git commit -m "feat: 添加任务执行器与 Bot 通知器"
```

---

### Task 13: Bot 命令处理 bot/handlers.py

**Files:**
- Create: `src/tgdl/bot/handlers.py`
- Test: `tests/unit/test_handlers.py`

**Step 1: 写失败测试**

```python
from dataclasses import replace
from datetime import datetime, timezone

from tgdl.bot.handlers import BotHandlers
from tgdl.models import ChannelRef, MediaItem, MediaKind, TaskSpec, TaskStatus
from tgdl.progress import ProgressTracker
from tgdl.task_queue import TaskQueue


def _handlers(snapshot=None) -> tuple[BotHandlers, TaskQueue]:
    queue = TaskQueue(runner=None)  # type: ignore[arg-type]
    return BotHandlers(queue, lambda: snapshot), queue


async def test_dl_submits_task() -> None:
    handlers, queue = _handlers()
    reply = await handlers.handle_text("/dl https://t.me/chan --regex 4k")
    assert "任务 #1" in reply and "已加入队列" in reply
    assert queue.get(1) is not None


async def test_dl_error_returns_usage_hint() -> None:
    handlers, _ = _handlers()
    reply = await handlers.handle_text("/dl nope")
    assert reply.startswith("❌") and "/help" in reply


async def test_tasks_empty_and_listed() -> None:
    handlers, queue = _handlers()
    assert "没有任务" in await handlers.handle_text("/tasks")
    queue.submit(TaskSpec(link=ChannelRef(username="c"), raw_link="https://t.me/c"))
    reply = await handlers.handle_text("/tasks")
    assert "#1" in reply and "排队中" in reply


async def test_cancel_reports_result() -> None:
    handlers, queue = _handlers()
    assert "不存在" in await handlers.handle_text("/cancel 9")
    await handlers.handle_text("/dl https://t.me/chan")
    assert "已取消" in await handlers.handle_text("/cancel 1")
    assert queue.get(1).status is TaskStatus.CANCELLED


async def test_status_without_task() -> None:
    handlers, _ = _handlers()
    assert "没有正在下载" in await handlers.handle_text("/status")


async def test_status_renders_snapshot() -> None:
    item = MediaItem(message_id=1, date=datetime(2026, 1, 1, tzinfo=timezone.utc), kind=MediaKind.VIDEO, file_name="a.mp4", size=10)
    tracker = ProgressTracker(task_id=7, channel_title="@c", items=(item,))
    handlers, _ = _handlers(tracker.snapshot)
    assert "任务 #7" in await handlers.handle_text("/status")


async def test_help_and_unknown() -> None:
    handlers, _ = _handlers()
    assert "用法" in await handlers.handle_text("/help")
    assert "用法" in await handlers.handle_text("/start")
    assert "未知命令" in await handlers.handle_text("/wat")
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_handlers.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 最小实现**

`src/tgdl/bot/handlers.py`:
```python
"""Bot 命令分发：只处理 OWNER 发来的以 / 开头的消息。"""
from __future__ import annotations

import logging
from typing import Any, Callable

from telethon import events

from tgdl.bot.commands import HELP_TEXT, CommandError, parse_cancel, parse_dl, split_command
from tgdl.models import TaskState
from tgdl.progress import STATUS_LABEL, ProgressSnapshot, render_progress
from tgdl.task_queue import TaskQueue

log = logging.getLogger(__name__)

SnapshotFn = Callable[[], ProgressSnapshot | None]
COMMAND_PATTERN = r"^/"


class BotHandlers:
    def __init__(self, queue: TaskQueue, current_snapshot: SnapshotFn) -> None:
        self._queue = queue
        self._current_snapshot = current_snapshot

    async def handle_text(self, text: str) -> str:
        try:
            name, args = split_command(text)
            if name == "/dl":
                return self._dl(args)
            if name == "/tasks":
                return self._tasks()
            if name == "/cancel":
                return self._cancel(args)
            if name == "/status":
                return self._status()
            if name in ("/help", "/start"):
                return HELP_TEXT
            return "未知命令，发送 /help 查看用法"
        except CommandError as exc:
            return f"❌ {exc}\n\n发送 /help 查看用法"

    def register(self, bot_client: Any, owner_id: int) -> None:
        @bot_client.on(events.NewMessage(from_users=owner_id, incoming=True, pattern=COMMAND_PATTERN))
        async def _on_command(event: Any) -> None:
            log.info("收到命令: %s", event.raw_text)
            await event.reply(await self.handle_text(event.raw_text))

    def _dl(self, args: list[str]) -> str:
        state = self._queue.submit(parse_dl(args))
        position = len(self._queue.active())
        return f"✅ 已加入队列，任务 #{state.task_id}（队列位置 {position}）\n{state.spec.raw_link}"

    def _tasks(self) -> str:
        active = self._queue.active()
        if not active:
            return "当前没有任务"
        return "📋 任务列表\n" + "\n".join(self._format_task(s) for s in active)

    def _format_task(self, state: TaskState) -> str:
        label = STATUS_LABEL[state.status]
        target = state.channel_title or state.spec.raw_link
        detail = f"  {len(state.results)}/{len(state.items)}" if state.items else ""
        return f"#{state.task_id} {label}  {target}{detail}"

    def _cancel(self, args: list[str]) -> str:
        task_id = parse_cancel(args)
        if self._queue.cancel(task_id):
            return f"🚫 任务 #{task_id} 已取消"
        return f"任务 #{task_id} 不存在或已结束"

    def _status(self) -> str:
        snapshot = self._current_snapshot()
        if snapshot is None:
            return "当前没有正在下载的任务"
        return render_progress(snapshot)
```

**Step 4: 运行确认通过**

Run: `uv run pytest tests/unit/test_handlers.py -q`
Expected: `7 passed`

**Step 5: 提交**

```bash
git add src/tgdl/bot/handlers.py tests/unit/test_handlers.py
git commit -m "feat: 添加 Bot 命令处理器"
```

---

### Task 14: 日志、装配与入口 main.py

**Files:**
- Create: `src/tgdl/logging_setup.py`
- Create: `src/tgdl/main.py`
- Test: `tests/unit/test_main_wiring.py`

**Step 1: 写失败测试（只测纯函数部分）**

```python
from pathlib import Path

from tgdl.config import Settings
from tgdl.main import build_worker_config, session_paths


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, api_id=1, api_hash="h", bot_token="t", owner_id=1,
                    data_dir=tmp_path / "data", download_dir=tmp_path / "dl", concurrency=4)


def test_session_paths_under_data_dir(tmp_path: Path) -> None:
    user, bot = session_paths(_settings(tmp_path))
    assert user == str(tmp_path / "data" / "user")
    assert bot == str(tmp_path / "data" / "bot")


def test_build_worker_config(tmp_path: Path) -> None:
    config = build_worker_config(_settings(tmp_path))
    assert config.download_dir == tmp_path / "dl"
    assert config.concurrency == 4
```

**Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_main_wiring.py -q`
Expected: FAIL，`ModuleNotFoundError`

**Step 3: 实现日志与入口**

`src/tgdl/logging_setup.py`:
```python
"""日志：控制台 + 按天轮转文件。"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
BACKUP_DAYS = 14


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(log_dir / "tgdl.log", when="midnight", backupCount=BACKUP_DAYS, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=[logging.StreamHandler(), file_handler])
    logging.getLogger("telethon").setLevel(logging.WARNING)
```

`src/tgdl/main.py`:
```python
"""装配所有组件并启动：用户客户端登录、Bot 客户端登录、队列循环。"""
from __future__ import annotations

import asyncio
import logging
import sys

from telethon import TelegramClient

from tgdl.bot.handlers import BotHandlers
from tgdl.bot.notifier import TelegramNotifier
from tgdl.config import ConfigError, Settings, load_settings
from tgdl.logging_setup import setup_logging
from tgdl.paths import cleanup_parts
from tgdl.task_queue import TaskQueue
from tgdl.worker import TaskWorker, WorkerConfig

log = logging.getLogger(__name__)


def session_paths(settings: Settings) -> tuple[str, str]:
    return str(settings.data_dir / "user"), str(settings.data_dir / "bot")


def build_worker_config(settings: Settings) -> WorkerConfig:
    return WorkerConfig(
        download_dir=settings.download_dir, concurrency=settings.concurrency,
        max_retries=settings.max_retries, progress_interval=settings.progress_interval,
    )


def build_clients(settings: Settings) -> tuple[TelegramClient, TelegramClient]:
    user_session, bot_session = session_paths(settings)
    proxy = settings.proxy()
    user = TelegramClient(user_session, settings.api_id, settings.api_hash, proxy=proxy)
    bot = TelegramClient(bot_session, settings.api_id, settings.api_hash, proxy=proxy)
    return user, bot


async def main_async(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(settings.data_dir / "logs")
    removed = cleanup_parts(settings.download_dir)
    if removed:
        log.info("清理残留 .part 文件 %d 个", removed)

    user, bot = build_clients(settings)
    await user.start()  # 首次运行会在终端交互输入手机号与验证码
    await bot.start(bot_token=settings.bot_token)
    log.info("用户与 Bot 客户端已登录，代理: %s", "已启用" if settings.proxy() else "直连")

    notifier = TelegramNotifier(bot, settings.owner_id)
    worker = TaskWorker(user, notifier, build_worker_config(settings))
    queue = TaskQueue(worker.run)
    BotHandlers(queue, worker.current_snapshot).register(bot, settings.owner_id)
    await notifier.send("✅ tgdl 已启动，发送 /help 查看用法")
    try:
        await asyncio.gather(queue.run_forever(), bot.run_until_disconnected())
    finally:
        await user.disconnect()
        await bot.disconnect()


def run() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"启动失败: {exc}\n请参考 .env.example 创建 .env", file=sys.stderr)
        sys.exit(1)
    try:
        asyncio.run(main_async(settings))
    except KeyboardInterrupt:
        print("已退出")


if __name__ == "__main__":
    run()
```

**Step 4: 运行确认通过 + 全量测试 + 覆盖率**

Run: `uv run pytest -q --cov --cov-report=term-missing`
Expected: 全部通过，覆盖率 ≥ 80%（main、logging_setup、notifier 已排除）。若不足，补齐对应模块的边界用例，不降低阈值。

**Step 5: 提交**

```bash
git add src/tgdl/main.py src/tgdl/logging_setup.py tests/unit/test_main_wiring.py
git commit -m "feat: 添加入口装配与日志配置"
```

---

### Task 15: README、端到端冒烟脚本与真实联调

**Files:**
- Create: `README.md`
- Create: `scripts/e2e_smoke.py`

**Step 1: 写 README.md**

内容包含：
1. 项目简介与架构一句话说明（用户账号抓取 + Bot 交互）。
2. 准备工作：申请 api_id/api_hash、BotFather 创建 Bot、查询自己的 user id、启动本地 SOCKS5 代理。
3. 安装：`uv sync`，复制 `.env.example` 为 `.env` 并填写。
4. 首次登录：`uv run tgdl`，按提示输入手机号与验证码；之后 session 持久化免登录。
5. 命令用法（复制 HELP_TEXT 内容）与示例。
6. 文件存放结构说明。
7. 常见问题：FloodWait、频道无权访问、代理不通时的排查。
8. 开发：`uv run pytest --cov`。

**Step 2: 写 scripts/e2e_smoke.py**

```python
"""手动端到端冒烟：对真实频道跑一次小任务，不进 CI。

用法: uv run python scripts/e2e_smoke.py https://t.me/somechannel --ids 1-5
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from tgdl.bot.commands import parse_dl
from tgdl.config import load_settings
from tgdl.main import build_clients, build_worker_config
from tgdl.models import TaskState
from tgdl.worker import TaskWorker


class PrintNotifier:
    async def send(self, text: str) -> int:
        print(text)
        return 0

    async def edit(self, message_id: int, text: str) -> None:
        print("\n" + text)


async def main(args: list[str]) -> None:
    settings = load_settings()
    user, _ = build_clients(settings)
    await user.start()
    worker = TaskWorker(user, PrintNotifier(), build_worker_config(settings))
    state = TaskState(task_id=0, spec=parse_dl(args))
    final = await worker.run(state, lambda s: print(f"[state] {s.status.value}"))
    print(f"结果: {final.status.value}, 文件 {len(final.results)} 个")
    await user.disconnect()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
```

**Step 3: 真实联调（人工）**

1. 填好 `.env`，确认代理端口可用：`curl -x socks5h://127.0.0.1:7890 https://api.telegram.org -I` 返回 200/302。
2. `uv run tgdl`，完成首次登录，Bot 应发来「已启动」。
3. 在 Bot 发送 `/dl https://t.me/<你有权限的公开频道> --ids <小范围>`，观察：概览消息、进度消息每 5 秒更新、最终汇总。
4. 发送 `/status`、`/tasks`，任务进行中发送 `/cancel 1`，确认 .part 被清理。
5. 再次发送同一命令，确认已存在文件被「跳过」。

**Step 4: 提交**

```bash
git add README.md scripts/e2e_smoke.py
git commit -m "docs: 添加 README 与端到端冒烟脚本"
```

---

## 完成标准

- `uv run pytest -q --cov` 全部通过，覆盖率 ≥ 80%。
- 真实联调 Step 3 五个步骤全部符合预期。
- 每个源文件 ≤ 300 行，无 print 调试残留，无硬编码密钥。
- `git log` 每个任务一个提交，信息符合 `<type>: <描述>`。
