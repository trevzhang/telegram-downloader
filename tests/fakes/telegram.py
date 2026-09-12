"""Telethon 客户端与消息的测试替身。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


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
    web_preview: object | None = None


@dataclass(frozen=True)
class FakeEntity:
    id: int
    title: str | None = None
    username: str | None = None


@dataclass
class FakeClient:
    """模拟 iter_messages / get_messages / download_media / get_entity 及原始请求调用。"""

    messages: tuple[FakeMessage, ...] = ()
    entity: FakeEntity | None = None
    entity_error: Exception | None = None
    entity_errors: list[Exception] = field(default_factory=list)  # 依次抛出，用完后回落到 entity_error/entity
    dialogs_unlock: bool = False  # True 时调用过 get_dialogs 后 get_entity 不再抛 entity_error
    dialogs_calls: int = 0
    request_results: dict[type, object] = field(default_factory=dict)  # 按请求类型返回结果或抛出异常
    entity_calls: list[Any] = field(default_factory=list)
    request_calls: list[Any] = field(default_factory=list)
    failures: list[Exception] = field(default_factory=list)  # 依次抛出，用完后正常下载
    chunk: int = 4
    delay: float = 0.0
    download_calls: list[int] = field(default_factory=list)
    max_concurrent: int = 0
    _in_flight: int = 0

    def __post_init__(self) -> None:
        self.messages = tuple(sorted(self.messages, key=lambda m: m.id))

    async def get_entity(self, ref: Any) -> FakeEntity | None:
        self.entity_calls.append(ref)
        if self.entity_errors:
            raise self.entity_errors.pop(0)
        if self.entity_error and not (self.dialogs_unlock and self.dialogs_calls):
            raise self.entity_error
        return self.entity

    async def get_dialogs(self) -> list[Any]:
        self.dialogs_calls += 1
        return []

    async def __call__(self, request: Any) -> Any:
        self.request_calls.append(request)
        result = self.request_results[type(request)]
        if isinstance(result, Exception):
            raise result
        return result

    async def iter_messages(
        self, entity: Any, reverse: bool = False, min_id: int = 0, max_id: int = 0, offset_date: datetime | None = None
    ) -> AsyncIterator[FakeMessage]:
        # 与 Telethon 一致：reverse 时 min_id 充当 offset_id，优先级高于 offset_date（后者被忽略）
        date_floor = None if min_id else offset_date
        selected = [
            m
            for m in self.messages
            if (min_id == 0 or m.id > min_id)
            and (max_id == 0 or m.id < max_id)
            and (date_floor is None or m.date > date_floor)
        ]
        for message in selected if reverse else reversed(selected):
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
                    handle.write(data[start : start + self.chunk])
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
