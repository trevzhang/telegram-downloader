from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from telethon.errors import (
    ChannelPrivateError,
    InviteHashExpiredError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import PeerChannel

from tests.fakes.telegram import FakeClient, FakeEntity, FakeFile, FakeMessage
from tgdl.filters import MediaFilter, compile_regex
from tgdl.models import ChannelRef, MediaKind, TaskSpec
from tgdl.scanner import ChannelAccessError, extract_media, iter_kwargs, resolve_channel, scan

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
    client = FakeClient(
        messages=(
            _msg(1, days=0, text="EP01", file=VIDEO),
            _msg(2, days=1, text="EP02", file=VIDEO),
            _msg(3, days=5, text="EP03", file=VIDEO),
        )
    )
    spec = _spec(date_from=T0 - timedelta(days=1), date_to=T0 + timedelta(days=2))
    items = await scan(client, object(), spec, MediaFilter(pattern=compile_regex("ep0[12]")))
    assert [i.message_id for i in items] == [1, 2]


async def test_scan_ids_range() -> None:
    client = FakeClient(messages=tuple(_msg(i, file=VIDEO) for i in range(1, 11)))
    items = await scan(client, object(), _spec(id_from=3, id_to=5), MediaFilter())
    assert [i.message_id for i in items] == [3, 4, 5]


async def test_scan_propagates_album_caption() -> None:
    client = FakeClient(
        messages=(
            _msg(1, text="Album 4K", file=PHOTO, grouped_id=77),
            _msg(2, text="", file=PHOTO, grouped_id=77),
            _msg(3, text="", file=PHOTO),
        )
    )
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


def test_extract_ignores_link_preview() -> None:
    assert extract_media(_msg(7, text="see https://x", file=IMG_DOC, web_preview=object())) is None


async def test_scan_enforces_date_from_locally_when_message_link_disables_offset_date() -> None:
    """带消息 ID 的链接会让 Telethon 忽略 offset_date，本地必须补上闭区间下界。"""
    client = FakeClient(
        messages=(
            _msg(1, days=0, file=VIDEO),
            _msg(2, days=1, file=VIDEO),
            _msg(3, days=2, file=VIDEO),
            _msg(4, days=2, file=VIDEO),
        )
    )
    spec = TaskSpec(link=ChannelRef(username="c", message_id=2), raw_link="x", date_from=T0 + timedelta(days=2))
    items = await scan(client, object(), spec, MediaFilter())
    assert [i.message_id for i in items] == [3, 4]


@dataclass(frozen=True)
class _Updates:
    chats: tuple[FakeEntity, ...] = ()


@dataclass(frozen=True)
class _JoinResult:
    updates: _Updates


@dataclass(frozen=True)
class _InviteInfo:
    chat: FakeEntity | None = None


INVITE = ChannelRef(invite_hash="abc")


async def test_resolve_invite_joins_and_returns_chat() -> None:
    entity = FakeEntity(id=5, title="secret")
    client = FakeClient(request_results={ImportChatInviteRequest: _JoinResult(_Updates((entity,)))})
    assert await resolve_channel(client, INVITE) is entity
    assert isinstance(client.request_calls[0], ImportChatInviteRequest)


async def test_resolve_invite_without_chats_raises() -> None:
    client = FakeClient(request_results={ImportChatInviteRequest: _JoinResult(_Updates())})
    with pytest.raises(ChannelAccessError, match="审批"):
        await resolve_channel(client, INVITE)


async def test_resolve_invite_already_participant_uses_check() -> None:
    entity = FakeEntity(id=5)
    client = FakeClient(
        request_results={
            ImportChatInviteRequest: UserAlreadyParticipantError(request=None),
            CheckChatInviteRequest: _InviteInfo(chat=entity),
        }
    )
    assert await resolve_channel(client, INVITE) is entity


async def test_resolve_invite_check_without_chat_raises() -> None:
    client = FakeClient(
        request_results={
            ImportChatInviteRequest: UserAlreadyParticipantError(request=None),
            CheckChatInviteRequest: _InviteInfo(),
        }
    )
    with pytest.raises(ChannelAccessError, match="无法获取"):
        await resolve_channel(client, INVITE)


@pytest.mark.parametrize(
    ("error", "match"),
    [(InviteHashExpiredError(request=None), "邀请链接无效"), (InviteRequestSentError(request=None), "审批")],
)
async def test_resolve_invite_maps_errors(error: Exception, match: str) -> None:
    client = FakeClient(request_results={ImportChatInviteRequest: error})
    with pytest.raises(ChannelAccessError, match=match):
        await resolve_channel(client, INVITE)


async def test_resolve_by_channel_id_uses_peer_channel() -> None:
    entity = FakeEntity(id=123)
    client = FakeClient(entity=entity)
    assert await resolve_channel(client, ChannelRef(channel_id=123)) is entity
    assert client.entity_calls == [PeerChannel(123)]


async def test_resolve_value_error_maps_to_access_error() -> None:
    client = FakeClient(entity_error=ValueError("no such"))
    with pytest.raises(ChannelAccessError, match="无法解析频道"):
        await resolve_channel(client, ChannelRef(username="c"))


async def test_resolve_channel_id_warms_dialogs_then_succeeds() -> None:
    """新 session 实体缓存为空时 get_entity 抛 ValueError，拉一次会话列表后重试应成功。"""
    entity = FakeEntity(id=123)
    client = FakeClient(entity=entity, entity_error=ValueError("cold cache"), dialogs_unlock=True)
    assert await resolve_channel(client, ChannelRef(channel_id=123)) is entity
    assert client.dialogs_calls == 1
    assert client.entity_calls == [PeerChannel(123), PeerChannel(123)]


async def test_resolve_channel_id_persistent_value_error_raises_access_error() -> None:
    client = FakeClient(entity_error=ValueError("still cold"))
    with pytest.raises(ChannelAccessError, match="已加入该频道"):
        await resolve_channel(client, ChannelRef(channel_id=123))
    assert client.dialogs_calls == 1
