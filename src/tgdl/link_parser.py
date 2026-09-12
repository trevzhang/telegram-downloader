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
