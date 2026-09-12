"""解析频道实体并扫描消息，产出待下载的 MediaItem 列表。"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from telethon.errors import (
    ChannelPrivateError, InviteHashExpiredError, InviteHashInvalidError, InviteRequestSentError,
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


NOT_A_MEMBER_MESSAGE = "无法解析频道，请确认账号已加入该频道"


def _chat_from_join_result(result: Any) -> Any:
    """ImportChatInviteRequest 返回 ChatInviteJoinResultOk(.updates.chats) 或 WebView（无 chats）。"""
    updates = getattr(result, "updates", result)
    chats = getattr(updates, "chats", None) or ()
    if not chats:
        raise ChannelAccessError("加入后未返回频道信息（可能需要管理员审批）")
    return chats[0]


async def _chat_from_invite_check(client: Any, invite_hash: str) -> Any:
    """已是成员时用 CheckChatInviteRequest 取频道；返回 ChatInviteAlready/ChatInvitePeek 才有 .chat，ChatInvite 没有。"""
    chat = getattr(await client(CheckChatInviteRequest(invite_hash)), "chat", None)
    if chat is None:
        raise ChannelAccessError("无法获取邀请链接对应的频道")
    return chat


async def _join_by_invite(client: Any, invite_hash: str) -> Any:
    try:
        return _chat_from_join_result(await client(ImportChatInviteRequest(invite_hash)))
    except UserAlreadyParticipantError:
        return await _chat_from_invite_check(client, invite_hash)
    except InviteRequestSentError as exc:
        raise ChannelAccessError("已发送加入申请，等待管理员审批后重试") from exc
    except (InviteHashExpiredError, InviteHashInvalidError) as exc:
        raise ChannelAccessError("邀请链接无效或已过期") from exc


async def _entity_by_channel_id(client: Any, channel_id: int) -> Any:
    """新 session 的实体缓存为空时 get_entity 会抛 ValueError；拉一次会话列表预热缓存后重试。"""
    peer = PeerChannel(channel_id)
    try:
        return await client.get_entity(peer)
    except ValueError:
        log.info("实体缓存未命中，拉取会话列表后重试: %s", channel_id)
    await client.get_dialogs()
    try:
        return await client.get_entity(peer)
    except ValueError as exc:
        raise ChannelAccessError(NOT_A_MEMBER_MESSAGE) from exc


async def resolve_channel(client: Any, ref: ChannelRef) -> Any:
    try:
        if ref.invite_hash:
            return await _join_by_invite(client, ref.invite_hash)
        if ref.channel_id is not None:
            return await _entity_by_channel_id(client, ref.channel_id)
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
    if getattr(message, "web_preview", None) is not None:  # 链接预览不是频道自身的媒体
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
        if spec.date_from is not None and message.date < spec.date_from:  # 服务端 offset_date 仅是优化
            continue
        item = extract_media(message)
        if item is not None:
            collected.append((message, item))
    items = _propagate_album_captions(tuple(collected))
    matched = tuple(item for item in items if media_filter.matches(item))
    log.info("扫描完成: 共 %d 个媒体，匹配 %d 个", len(items), len(matched))
    return matched
