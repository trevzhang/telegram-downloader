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


def _has_valid_chars(name: str) -> bool:
    """去掉非法字符及首尾的空格/点后是否还有内容。"""
    return bool(_INVALID_CHARS.sub("", name).strip(" ."))


def sanitize_filename(name: str) -> str:
    if not _has_valid_chars(name):
        return DEFAULT_NAME
    cleaned = _INVALID_CHARS.sub("_", name).strip(" .")
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
