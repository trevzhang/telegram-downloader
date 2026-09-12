"""过滤器：正则、时间范围、序号范围，以及 TaskSpec 的互斥校验。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time

import regex

from tgdl.models import MAX_MESSAGE_ID, MIN_MESSAGE_ID, MediaItem, MediaKind, TaskSpec

logger = logging.getLogger(__name__)

# 单次正则匹配上限，防止灾难性回溯拖垮事件循环
REGEX_TIMEOUT_SECONDS = 1.0


class FilterError(ValueError):
    """过滤参数不合法。"""


_DATE_ONLY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DATE_TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?$")


def parse_datetime(text: str, *, end_of_day: bool = False) -> datetime:
    """解析 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM[:SS]。无时区按本地时区理解，返回 UTC aware 时间。"""
    cleaned = text.strip()
    if not (_DATE_ONLY.match(cleaned) or _DATE_TIME.match(cleaned)):
        raise FilterError(f"日期格式无效: {text}，应为 2026-01-01 或 2026-01-01T12:00")
    try:
        value = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise FilterError(f"日期格式无效: {text}，应为 2026-01-01 或 2026-01-01T12:00") from exc
    if end_of_day and _DATE_ONLY.match(cleaned):
        value = datetime.combine(value.date(), time.max)
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(UTC)


def compile_regex(text: str) -> regex.Pattern[str]:
    try:
        return regex.compile(text, regex.IGNORECASE)
    except regex.error as exc:
        raise FilterError(f"正则表达式无效: {text} ({exc})") from exc


def _validate_id_bounds(spec: TaskSpec) -> None:
    for value in (spec.id_from, spec.id_to):
        if value is not None and not MIN_MESSAGE_ID <= value <= MAX_MESSAGE_ID:
            raise FilterError(f"--ids 序号必须在 {MIN_MESSAGE_ID} 到 {MAX_MESSAGE_ID} 之间")


def validate_spec(spec: TaskSpec) -> None:
    has_date = spec.date_from is not None or spec.date_to is not None
    has_ids = spec.id_from is not None or spec.id_to is not None
    if has_date and has_ids:
        raise FilterError("--from/--to 与 --ids 不能同时使用")
    if spec.date_from and spec.date_to and spec.date_from > spec.date_to:
        raise FilterError("--from 不能晚于 --to")
    _validate_id_bounds(spec)
    if spec.id_from is not None and spec.id_to is not None and spec.id_from > spec.id_to:
        raise FilterError("--ids 起始序号不能大于结束序号")
    if spec.regex is not None:
        compile_regex(spec.regex)


def _search_with_timeout(pattern: regex.Pattern[str], text: str) -> bool:
    try:
        return pattern.search(text, timeout=REGEX_TIMEOUT_SECONDS) is not None
    except TimeoutError:
        logger.warning("正则匹配超时（%.1fs），按不匹配处理: pattern=%r", REGEX_TIMEOUT_SECONDS, pattern.pattern)
        return False


@dataclass(frozen=True)
class MediaFilter:
    kind: MediaKind = MediaKind.ALL
    pattern: regex.Pattern[str] | None = None

    def matches(self, item: MediaItem) -> bool:
        if self.kind is not MediaKind.ALL and item.kind is not self.kind:
            return False
        if self.pattern is None:
            return True
        return _search_with_timeout(self.pattern, item.caption) or _search_with_timeout(self.pattern, item.file_name)


def build_filter(spec: TaskSpec) -> MediaFilter:
    pattern = compile_regex(spec.regex) if spec.regex else None
    return MediaFilter(kind=spec.kind, pattern=pattern)
