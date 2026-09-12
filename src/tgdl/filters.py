"""过滤器：正则、时间范围、序号范围，以及 TaskSpec 的互斥校验。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timezone

from tgdl.models import MediaItem, MediaKind, TaskSpec


class FilterError(ValueError):
    """过滤参数不合法。"""


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_datetime(text: str, *, end_of_day: bool = False) -> datetime:
    """解析 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM。无时区按本地时区理解，返回 UTC aware 时间。"""
    cleaned = text.strip()
    try:
        value = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise FilterError(f"日期格式无效: {text}，应为 2026-01-01 或 2026-01-01T12:00") from exc
    if end_of_day and _DATE_ONLY.match(cleaned):
        value = datetime.combine(value.date(), time.max)
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc)


def compile_regex(text: str) -> re.Pattern[str]:
    try:
        return re.compile(text, re.IGNORECASE)
    except re.error as exc:
        raise FilterError(f"正则表达式无效: {text} ({exc})") from exc


def validate_spec(spec: TaskSpec) -> None:
    has_date = spec.date_from is not None or spec.date_to is not None
    has_ids = spec.id_from is not None or spec.id_to is not None
    if has_date and has_ids:
        raise FilterError("--from/--to 与 --ids 不能同时使用")
    if spec.date_from and spec.date_to and spec.date_from > spec.date_to:
        raise FilterError("--from 不能晚于 --to")
    if spec.id_from is not None and spec.id_to is not None and spec.id_from > spec.id_to:
        raise FilterError("--ids 起始序号不能大于结束序号")
    if spec.regex is not None:
        compile_regex(spec.regex)


@dataclass(frozen=True)
class MediaFilter:
    kind: MediaKind = MediaKind.ALL
    pattern: re.Pattern[str] | None = None

    def matches(self, item: MediaItem) -> bool:
        if self.kind is not MediaKind.ALL and item.kind is not self.kind:
            return False
        if self.pattern is None:
            return True
        return bool(self.pattern.search(item.caption) or self.pattern.search(item.file_name))


def build_filter(spec: TaskSpec) -> MediaFilter:
    pattern = compile_regex(spec.regex) if spec.regex else None
    return MediaFilter(kind=spec.kind, pattern=pattern)
