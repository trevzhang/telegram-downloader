import logging
import os
import time as time_module
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from tgdl.filters import FilterError, compile_filter, parse_date_literal, validate_spec
from tgdl.models import ChannelRef, MediaItem, MediaKind, TaskSpec

T0 = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)


def _item(**kw: object) -> MediaItem:
    base: dict = {
        "message_id": 100,
        "date": T0,
        "kind": MediaKind.VIDEO,
        "file_name": "EP01 饼干姐姐.mp4",
        "size": 50 * 1024 * 1024,
        "caption": "#饼干姐姐 第一集",
        "ext": ".mp4",
    }
    return MediaItem(**{**base, **kw})  # type: ignore[arg-type]


def _ok(expr: str, **kw: object) -> bool:
    return compile_filter(expr).matches(_item(**kw))


def test_empty_filter_matches_everything() -> None:
    assert compile_filter(None).matches(_item()) and compile_filter("  ").matches(_item())


def test_caption_regex_and_plain_equality() -> None:
    assert _ok("caption == r'.*#饼干姐姐.*'")
    assert not _ok("caption == r'#饼干姐姐'")  # 正则需整体匹配
    assert _ok("message_caption != r'.*#三体.*'")
    assert _ok("caption == '#饼干姐姐 第一集'") and not _ok("caption == '#饼干姐姐'")
    assert _ok('caption == r".*第一集"')


def test_file_name_type_extension_fields() -> None:
    assert _ok("file_name == r'.*EP0\\d.*'") and _ok("media_file_name == r'.*\\.mp4'")
    assert _ok("media_type == 'video'") and not _ok("media_type == 'photo'")
    assert _ok("file_extension == r'(mp4|mp3)'")
    assert _ok("file_name == '7.jpg'", file_name="", ext=".jpg", message_id=7)


def test_size_with_units_and_products() -> None:
    assert _ok("file_size >= 1KB and file_size <= 100MB")
    assert _ok("media_file_size >= 10 * 1024 * 1024 && file_size < 1GB")
    assert not _ok("file_size > 60MB")
    assert _ok("file_size == 52428800")


def test_id_range_and_logic_with_parentheses() -> None:
    assert _ok("id >= 1 && id <= 900")
    assert not _ok("message_id > 100 or message_id < 100")
    assert _ok("(caption == r'.*#三体.*' or caption == r'.*饼干.*') and file_size > 1MB")
    assert not _ok("caption == r'.*#三体.*' or caption == r'.*饼干.*' and file_size > 1GB")


def test_date_literals_precisions_and_aliases() -> None:
    assert _ok("message_date >= 2026-05-10 and message_date <= 2026-09-15")
    assert _ok("message_date > 2026.05 and message_date < 2026.06")
    assert _ok("message_date_time > 2026/05/10 00:00 && message_date_time < 2026-05-10 23:59:59")
    assert not _ok("message_date < 2026-05-10")


def test_parse_date_literal_uses_local_timezone(shanghai_tz: None) -> None:
    assert parse_date_literal("2026-05-10 08:00") == datetime(2026, 5, 10, 0, 0, tzinfo=UTC)
    assert parse_date_literal("2026.05") == datetime(2026, 4, 30, 16, 0, tzinfo=UTC)


@pytest.fixture
def shanghai_tz() -> Iterator[None]:
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    time_module.tzset()
    yield
    if old is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old
    time_module.tzset()


@pytest.mark.parametrize(
    "expr",
    [
        "caption == 123",
        "caption > 'a'",
        "file_size == 'big'",
        "message_date >= 'yesterday'",
        "unknown_field == 1",
        "id >= 1 and",
        "(id >= 1",
        "id >= 1 && id <= 2 extra",
        "caption == r'(unclosed'",
        "id ~ 1",
        "message_date >= 2026-13-01",
    ],
)
def test_invalid_expressions_raise(expr: str) -> None:
    with pytest.raises(FilterError):
        compile_filter(expr)


def test_pathological_regex_times_out(caplog: pytest.LogCaptureFixture) -> None:
    flt = compile_filter("caption == r'^(a|aa)+$'")
    with caplog.at_level(logging.WARNING):
        assert flt.matches(_item(caption="a" * 40 + "b")) is False
    assert "超时" in caplog.text


def _spec(**kw: object) -> TaskSpec:
    return TaskSpec(link=ChannelRef(username="c"), raw_link="x", **kw)  # type: ignore[arg-type]


def test_validate_spec_checks_ids_and_filter() -> None:
    validate_spec(_spec(id_from=1, id_to=None, filter_expr="id > 3"))
    with pytest.raises(FilterError, match="起始序号"):
        validate_spec(_spec(id_from=5, id_to=3))
    with pytest.raises(FilterError):
        validate_spec(_spec(id_from=0))
    with pytest.raises(FilterError):
        validate_spec(_spec(filter_expr="caption == 1"))
