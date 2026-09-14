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
MONTH_DIR_FORMAT = "%Y_%m"  # 月份子目录形如 2026_09
NAME_SEPARATOR = " - "  # 消息 ID 与文件名之间的分隔，与旧工具一致
_NEWLINES = re.compile(r"\s*[\r\n]+\s*")
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
    """目录名优先用频道/群组的显示名称，与手工整理的目录习惯一致；没有名称时退回用户名，再退回 ID。"""
    title = getattr(entity, "title", None)
    username = getattr(entity, "username", None)
    return sanitize_filename(title or username or str(entity.id))


def caption_as_name(caption: str) -> str:
    """消息文本转文件名主干：换行折叠为 `_`（与旧工具 validate_title 一致），去掉首尾空白。"""
    return _NEWLINES.sub("_", caption.strip())


def file_name_for(item: MediaItem) -> str:
    """与 telegram_media_downloader 的命名一致：`<消息ID> - <文件名>`；
    没有文件名时用消息文本补位；再没有则只剩 `<消息ID><扩展名>`。"""
    stem = item.file_name or caption_as_name(item.caption)
    if not stem:
        return f"{item.message_id}{item.ext}"
    if not item.file_name:
        stem += item.ext
    return f"{item.message_id}{NAME_SEPARATOR}{sanitize_filename(stem)}"


def target_path(root: Path, channel_dir: str, item: MediaItem) -> Path:
    return root / channel_dir / item.date.strftime(MONTH_DIR_FORMAT) / file_name_for(item)


def part_path(path: Path) -> Path:
    return path.with_name(path.name + PART_SUFFIX)


class DownloadDirMissingError(RuntimeError):
    """下载根目录不存在（常见原因：NAS 尚未挂载或已掉线）。"""


DOWNLOAD_DIR_MISSING_MESSAGE = "下载目录不存在（NAS 未挂载？）："


def ensure_download_root(root: Path) -> None:
    """下载根目录必须已存在；不自动创建，避免 NAS 掉线时把文件写到本机磁盘。"""
    if not root.is_dir():
        raise DownloadDirMissingError(f"{DOWNLOAD_DIR_MISSING_MESSAGE}{root}")
