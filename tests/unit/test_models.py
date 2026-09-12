from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tgdl.models import (
    MAX_MESSAGE_ID,
    MIN_MESSAGE_ID,
    ChannelRef,
    FileResult,
    FileStatus,
    MediaItem,
    MediaKind,
    TaskSpec,
    TaskState,
    TaskStatus,
)


def _spec() -> TaskSpec:
    return TaskSpec(link=ChannelRef(username="chan"), raw_link="https://t.me/chan")


def _item() -> MediaItem:
    return MediaItem(
        message_id=5, date=datetime(2026, 1, 1, tzinfo=UTC), kind=MediaKind.PHOTO, file_name="a.jpg", size=10
    )


@pytest.mark.parametrize(
    "instance, attr",
    [
        (ChannelRef(username="chan"), "username"),
        (_spec(), "regex"),
        (_item(), "size"),
        (FileResult(item=_item(), path=Path("a.jpg"), status=FileStatus.DONE), "error"),
        (TaskState(task_id=1, spec=_spec()), "status"),
    ],
)
def test_models_are_frozen(instance: object, attr: str) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(instance, attr, "x")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"username": "chan"},
        {"channel_id": 123},
        {"invite_hash": "abc"},
        {"username": "chan", "message_id": 7},
    ],
)
def test_channel_ref_single_identifier_ok(kwargs: dict[str, object]) -> None:
    ChannelRef(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"username": "chan", "channel_id": 123},
        {"username": "chan", "channel_id": 123, "invite_hash": "abc"},
    ],
)
def test_channel_ref_requires_exactly_one_identifier(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="只能设置"):
        ChannelRef(**kwargs)  # type: ignore[arg-type]


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
    result = FileResult(item=_item(), path=Path("a.jpg"), status=FileStatus.DONE)
    assert result.error is None
    assert result.item.message_id == 5


def test_message_id_bounds() -> None:
    assert MIN_MESSAGE_ID == 1
    assert MAX_MESSAGE_ID == 2**31 - 1
