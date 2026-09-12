"""下载路径与文件名处理。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tgdl.models import MediaItem

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
MAX_NAME_LENGTH = 120
MAX_NAME_BYTES = 200
MAX_EXT_LENGTH = 10
PART_SUFFIX = ".part"
DEFAULT_NAME = "file"


def _has_valid_chars(name: str) -> bool:
    """去掉非法字符及首尾的空格/点后是否还有内容。"""
    return bool(_INVALID_CHARS.sub("", name).strip(" ."))


def _split_ext(name: str) -> tuple[str, str]:
    """拆出可保留的短扩展名；没有或过长时扩展名为空串。"""
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext) > MAX_EXT_LENGTH:
        return name, ""
    return stem, ext


def _join(stem: str, ext: str) -> str:
    stem = stem.strip(" .") or DEFAULT_NAME
    return f"{stem}.{ext}" if ext else stem


def _truncate_chars(name: str) -> str:
    if len(name) <= MAX_NAME_LENGTH:
        return name
    stem, ext = _split_ext(name)
    budget = MAX_NAME_LENGTH - (len(ext) + 1 if ext else 0)
    return _join(stem[:budget], ext)


def _truncate_bytes(name: str) -> str:
    """按 UTF-8 字节数截断，避免 CJK 文件名超出文件系统 255 字节限制。"""
    if len(name.encode("utf-8")) <= MAX_NAME_BYTES:
        return name
    stem, ext = _split_ext(name)
    budget = MAX_NAME_BYTES - (len(ext.encode("utf-8")) + 1 if ext else 0)
    return _join(stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore"), ext)


def sanitize_filename(name: str) -> str:
    if not _has_valid_chars(name):
        return DEFAULT_NAME
    cleaned = _INVALID_CHARS.sub("_", name).strip(" .")
    return _truncate_bytes(_truncate_chars(cleaned))


def channel_dir_name(entity: Any) -> str:
    username = getattr(entity, "username", None)
    title = getattr(entity, "title", None)
    return sanitize_filename(username or title or str(entity.id))


def target_path(root: Path, channel_dir: str, item: MediaItem) -> Path:
    file_name = sanitize_filename(item.file_name)
    return root / channel_dir / item.date.strftime("%Y-%m") / f"{item.message_id}_{file_name}"


def part_path(path: Path) -> Path:
    return path.with_name(path.name + PART_SUFFIX)


class DownloadDirMissingError(RuntimeError):
    """下载根目录不存在（常见原因：NAS 尚未挂载或已掉线）。"""


DOWNLOAD_DIR_MISSING_MESSAGE = "下载目录不存在（NAS 未挂载？）："


def ensure_download_root(root: Path) -> None:
    """下载根目录必须已存在；不自动创建，避免 NAS 掉线时把文件写到本机磁盘。"""
    if not root.is_dir():
        raise DownloadDirMissingError(f"{DOWNLOAD_DIR_MISSING_MESSAGE}{root}")


def cleanup_parts(root: Path) -> int:
    """删除残留的 .part 文件，返回删除数量。"""
    if not root.exists():
        return 0
    parts = tuple(root.rglob(f"*{PART_SUFFIX}"))
    for part in parts:
        part.unlink(missing_ok=True)
    return len(parts)
