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

# Telegram 消息序号为 32 位正整数
MIN_MESSAGE_ID = 1
MAX_MESSAGE_ID = 2**31 - 1


@dataclass(frozen=True)
class ChannelRef:
    """解析后的频道引用。三种标识互斥：username / channel_id / invite_hash。"""

    username: str | None = None
    channel_id: int | None = None
    invite_hash: str | None = None
    message_id: int | None = None

    def __post_init__(self) -> None:
        identifiers = (self.username, self.channel_id, self.invite_hash)
        if sum(value is not None for value in identifiers) != 1:
            raise ValueError("ChannelRef 必须且只能设置 username、channel_id、invite_hash 之一")


@dataclass(frozen=True)
class TaskSpec:
    """date_from / date_to 必须为 aware UTC datetime（由 filters.parse_datetime 保证）。"""

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
