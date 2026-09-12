import logging
import time as time_module
from collections.abc import Iterator
from datetime import UTC, datetime
from time import perf_counter

import pytest

from tgdl.filters import FilterError, MediaFilter, build_filter, compile_regex, parse_datetime, validate_spec
from tgdl.models import ChannelRef, MediaItem, MediaKind, TaskSpec


def _spec(**kwargs: object) -> TaskSpec:
    return TaskSpec(link=ChannelRef(username="c"), raw_link="https://t.me/c", **kwargs)  # type: ignore[arg-type]


def _item(kind: MediaKind = MediaKind.VIDEO, name: str = "a.mp4", caption: str = "") -> MediaItem:
    return MediaItem(
        message_id=1, date=datetime(2026, 1, 1, tzinfo=UTC), kind=kind, file_name=name, size=1, caption=caption
    )


def test_parse_date_only_is_utc_aware() -> None:
    value = parse_datetime("2026-03-01")
    assert value.tzinfo is not None
    assert value.utcoffset().total_seconds() == 0


def test_parse_date_only_end_of_day() -> None:
    start = parse_datetime("2026-03-01")
    end = parse_datetime("2026-03-01", end_of_day=True)
    assert (end - start).total_seconds() > 86399


def test_parse_datetime_with_time() -> None:
    value = parse_datetime("2026-03-01T12:30")
    assert value.tzinfo is not None


def test_parse_datetime_invalid() -> None:
    with pytest.raises(FilterError, match="日期格式无效"):
        parse_datetime("2026/03/01")


def test_compile_regex_invalid() -> None:
    with pytest.raises(FilterError, match="正则表达式无效"):
        compile_regex("(")


def test_validate_date_and_ids_conflict() -> None:
    with pytest.raises(FilterError, match="不能同时使用"):
        validate_spec(_spec(date_from=datetime(2026, 1, 1, tzinfo=UTC), id_from=1, id_to=2))


def test_validate_reversed_ids() -> None:
    with pytest.raises(FilterError, match="起始序号"):
        validate_spec(_spec(id_from=10, id_to=2))


def test_validate_reversed_dates() -> None:
    with pytest.raises(FilterError, match="不能晚于"):
        validate_spec(_spec(date_from=datetime(2026, 2, 1, tzinfo=UTC), date_to=datetime(2026, 1, 1, tzinfo=UTC)))


def test_validate_ok_passes() -> None:
    validate_spec(_spec(id_from=1, id_to=5, regex="abc"))


def test_filter_matches_caption_or_filename_case_insensitive() -> None:
    flt = MediaFilter(pattern=compile_regex("4k"))
    assert flt.matches(_item(caption="Movie 4K HDR"))
    assert flt.matches(_item(name="movie.4K.mp4"))
    assert not flt.matches(_item(caption="720p", name="x.mp4"))


def test_filter_by_kind() -> None:
    flt = MediaFilter(kind=MediaKind.PHOTO)
    assert flt.matches(_item(kind=MediaKind.PHOTO))
    assert not flt.matches(_item(kind=MediaKind.VIDEO))


def test_build_filter_from_spec() -> None:
    flt = build_filter(_spec(regex="ep\\d+", kind=MediaKind.VIDEO))
    assert flt.matches(_item(caption="EP01"))
    assert not flt.matches(_item(kind=MediaKind.PHOTO, caption="EP01"))


def test_pathological_regex_times_out_instead_of_hanging() -> None:
    flt = MediaFilter(pattern=compile_regex("(a+)+$"))
    started = perf_counter()
    matched = flt.matches(_item(caption="a" * 40 + "b", name="x.mp4"))
    elapsed = perf_counter() - started
    assert matched is False
    assert elapsed < 5


def test_regex_timeout_logs_warning_and_returns_false(caplog: pytest.LogCaptureFixture) -> None:
    flt = MediaFilter(pattern=compile_regex("^(a|aa)+$"))
    started = perf_counter()
    with caplog.at_level(logging.WARNING, logger="tgdl.filters"):
        matched = flt.matches(_item(caption="a" * 40 + "b", name="x.mp4"))
    assert matched is False
    assert perf_counter() - started < 5
    assert any("^(a|aa)+$" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("bounds", [(0, 5), (1, 99999999999)])
def test_validate_ids_out_of_bounds(bounds: tuple[int, int]) -> None:
    with pytest.raises(FilterError, match="1 到 2147483647"):
        validate_spec(_spec(id_from=bounds[0], id_to=bounds[1]))


def test_parse_datetime_rejects_compact_digits() -> None:
    with pytest.raises(FilterError, match="日期格式无效"):
        parse_datetime("20260101")


def test_parse_datetime_accepts_seconds() -> None:
    assert parse_datetime("2026-03-01T12:30:15").tzinfo is not None


@pytest.fixture
def shanghai_tz(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time_module.tzset()
    yield
    monkeypatch.undo()
    time_module.tzset()


def test_parse_datetime_uses_local_timezone(shanghai_tz: None) -> None:
    assert parse_datetime("2026-03-01") == datetime(2026, 2, 28, 16, 0, tzinfo=UTC)
    assert parse_datetime("2026-03-01", end_of_day=True).isoformat() == "2026-03-01T15:59:59.999999+00:00"
