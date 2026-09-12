"""解析 Telegram 复制链接为 ChannelRef。"""

from __future__ import annotations

import re

from tgdl.models import MAX_MESSAGE_ID, MIN_MESSAGE_ID, ChannelRef


class LinkParseError(ValueError):
    """无法识别的链接。"""


_HOST = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(.+)$", re.IGNORECASE)
_PRIVATE = re.compile(r"^c/([0-9]{1,10})(?:/([0-9]{1,10}))?$")
_INVITE = re.compile(r"^(?:\+|joinchat/)([A-Za-z0-9_-]+)$")
_PUBLIC = re.compile(r"^([A-Za-z][A-Za-z0-9_]{3,31})(?:/([0-9]{1,10}))?$")


def _opt_message_id(value: str | None, raw: str) -> int | None:
    if not value:
        return None
    message_id = int(value)
    if not MIN_MESSAGE_ID <= message_id <= MAX_MESSAGE_ID:
        raise LinkParseError(f"链接中的消息序号超出范围: {raw!r}")
    return message_id


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
        return ChannelRef(channel_id=int(match.group(1)), message_id=_opt_message_id(match.group(2), raw))
    if match := _INVITE.match(path):
        return ChannelRef(invite_hash=match.group(1))
    if match := _PUBLIC.match(path):
        return ChannelRef(username=match.group(1), message_id=_opt_message_id(match.group(2), raw))
    raise LinkParseError(f"无法识别的链接: {raw!r}")
