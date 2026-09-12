from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tgdl.models import (
    ChannelRef, FileResult, FileStatus, MediaItem, MediaKind, TaskSpec, TaskState, TaskStatus,
)


def _spec() -> TaskSpec:
    return TaskSpec(link=ChannelRef(username="chan"), raw_link="https://t.me/chan")


def test_task_spec_is_frozen() -> None:
    spec = _spec()
    with pytest.raises(FrozenInstanceError):
        spec.regex = "x"  # type: ignore[misc]


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
    item = MediaItem(message_id=5, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                     kind=MediaKind.PHOTO, file_name="a.jpg", size=10)
    result = FileResult(item=item, path=Path("a.jpg"), status=FileStatus.DONE)
    assert result.error is None
    assert result.item.message_id == 5
