"""过滤表达式：与 telegram_media_downloader 的 download_filter 语法保持一致。

示例：caption == r'.*#饼干姐姐.*' and date >= 2026-05-10 and file_size < 100MB
字段：id/message_id、caption/message_caption、file_name/media_file_name、file_size/media_file_size、
      media_type、file_extension、date/message_date/message_date_time
运算：> < >= <= == !=，and/&&，or/||，括号；字符串加引号，r'...' 为正则（整体匹配）；大小单位 KB/MB/GB/TB。
"""

from __future__ import annotations

import logging
import operator
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import regex

from tgdl.models import MAX_MESSAGE_ID, MIN_MESSAGE_ID, MediaItem, TaskSpec

logger = logging.getLogger(__name__)

REGEX_TIMEOUT_SECONDS = 1.0  # 单次正则匹配上限，防止灾难性回溯拖垮事件循环
Predicate = Callable[[MediaItem], bool]
Getter = Callable[[MediaItem], Any]


class FilterError(ValueError):
    """过滤表达式不合法。"""


_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
_INT_FIELDS: dict[str, Getter] = {
    "message_id": lambda i: i.message_id,
    "media_file_size": lambda i: i.size,
}
_STR_FIELDS: dict[str, Getter] = {
    "message_caption": lambda i: i.caption,
    "media_file_name": lambda i: i.file_name or f"{i.message_id}{i.ext}",
    "media_type": lambda i: i.kind.value,
    "file_extension": lambda i: i.ext.lstrip("."),
}
_DATE_FIELDS: dict[str, Getter] = {"message_date": lambda i: i.date}
_ALIASES = {
    "id": "message_id",
    "caption": "message_caption",
    "file_name": "media_file_name",
    "file_size": "media_file_size",
    "message_date_time": "message_date",
    "date": "message_date",
}
_COMPARE: dict[str, Callable[[Any, Any], bool]] = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}
_TOKEN = re.compile(
    r"""(?P<ws>\s+)
      |(?P<date>\d{4}[.\-/]\d{1,2}(?:[.\-/]\d{1,2})?(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)
      |(?P<num>\d+)\s*(?P<unit>[KMGT]?B)?(?![\w.])
      |(?P<str>r?'[^']*'|r?"[^"]*")
      |(?P<op>>=|<=|==|!=|>|<|&&|\|\||\(|\)|\*)
      |(?P<word>[A-Za-z_][A-Za-z_0-9]*)""",
    re.VERBOSE | re.IGNORECASE,
)


@dataclass(frozen=True)
class _Tok:
    kind: str
    value: Any


def _tokenize(text: str) -> tuple[_Tok, ...]:
    tokens: list[_Tok] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise FilterError(f"过滤表达式在第 {pos + 1} 个字符附近无法解析: {text[pos : pos + 12]!r}")
        pos = match.end()
        kind = match.lastgroup or ""
        if kind == "ws":
            continue
        if kind == "num":
            unit = (match.group("unit") or "B").upper()
            tokens.append(_Tok("num", int(match.group("num")) * _UNITS[unit]))
        elif kind == "unit":  # (?P<unit>) 可能成为 lastgroup
            unit = (match.group("unit") or "B").upper()
            tokens.append(_Tok("num", int(match.group("num")) * _UNITS[unit]))
        elif kind == "word" and match.group().lower() in ("and", "or"):
            tokens.append(_Tok("op", "&&" if match.group().lower() == "and" else "||"))
        else:
            tokens.append(_Tok(kind, match.group()))
    return tuple(tokens)


def parse_date_literal(text: str) -> datetime:
    """年/月/日用 . - / 分隔，可省略日和时间；无时区按本地时区理解，返回 UTC aware 时间。"""
    parts = re.split(r"[ T]", text.strip(), maxsplit=1)
    ymd = [int(p) for p in re.split(r"[.\-/]", parts[0])]
    while len(ymd) < 3:
        ymd.append(1)
    hms = [int(p) for p in parts[1].split(":")] if len(parts) > 1 else []
    while len(hms) < 3:
        hms.append(0)
    try:
        value = datetime(ymd[0], ymd[1], ymd[2], hms[0], hms[1], hms[2])
    except ValueError as exc:
        raise FilterError(f"日期无效: {text}") from exc
    return value.astimezone().astimezone(UTC)


def _regex_fullmatch(pattern: regex.Pattern[str], text: str) -> bool:
    try:
        return pattern.fullmatch(text, timeout=REGEX_TIMEOUT_SECONDS) is not None
    except TimeoutError:
        logger.warning("正则匹配超时（%.1fs），按不匹配处理: pattern=%r", REGEX_TIMEOUT_SECONDS, pattern.pattern)
        return False


def _string_matcher(literal: str) -> Callable[[str], bool]:
    is_regex = literal[0] in "rR"
    body = literal[2:-1] if is_regex else literal[1:-1]
    if not is_regex:
        return lambda value: value == body
    try:
        pattern = regex.compile(body)
    except regex.error as exc:
        raise FilterError(f"正则表达式无效: {body} ({exc})") from exc
    return lambda value: _regex_fullmatch(pattern, value)


class _Parser:
    def __init__(self, tokens: tuple[_Tok, ...]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> Predicate:
        predicate = self._or()
        if self._pos != len(self._tokens):
            raise FilterError(f"过滤表达式多余的内容: {self._peek().value!r}")
        return predicate

    def _peek(self) -> _Tok:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else _Tok("end", "")

    def _take(self, kind: str | None = None) -> _Tok:
        tok = self._peek()
        if tok.kind == "end" or (kind is not None and tok.kind != kind):
            raise FilterError(f"过滤表达式不完整或语法错误，位于 {tok.value!r}")
        self._pos += 1
        return tok

    def _or(self) -> Predicate:
        left = self._and()
        while self._peek().value == "||":
            self._take()
            right = self._and()
            left = _either(left, right)
        return left

    def _and(self) -> Predicate:
        left = self._comparison()
        while self._peek().value == "&&":
            self._take()
            right = self._comparison()
            left = _both(left, right)
        return left

    def _comparison(self) -> Predicate:
        if self._peek().value == "(":
            self._take()
            inner = self._or()
            if self._take().value != ")":
                raise FilterError("过滤表达式括号不匹配")
            return inner
        field = self._take("word").value.lower()
        name = _ALIASES.get(field, field)
        op = self._take("op").value
        if op not in _COMPARE:
            raise FilterError(f"不支持的运算符: {op}")
        if name in _INT_FIELDS:
            return _compare(_INT_FIELDS[name], _COMPARE[op], self._product())
        if name in _DATE_FIELDS:
            return _compare(_DATE_FIELDS[name], _COMPARE[op], parse_date_literal(self._take("date").value))
        if name in _STR_FIELDS:
            if op not in ("==", "!="):
                raise FilterError(f"字符串字段 {field} 只支持 == 和 !=")
            matcher = _string_matcher(self._take("str").value)
            getter = _STR_FIELDS[name]
            return (lambda i: matcher(getter(i))) if op == "==" else (lambda i: not matcher(getter(i)))
        raise FilterError(f"不支持的字段: {field}")

    def _product(self) -> int:
        value = self._take("num").value
        while self._peek().value == "*":
            self._take()
            value *= self._take("num").value
        return int(value)


def _compare(getter: Getter, op: Callable[[Any, Any], bool], right: Any) -> Predicate:
    return lambda item: op(getter(item), right)


def _both(a: Predicate, b: Predicate) -> Predicate:
    return lambda item: a(item) and b(item)


def _either(a: Predicate, b: Predicate) -> Predicate:
    return lambda item: a(item) or b(item)


@dataclass(frozen=True)
class MediaFilter:
    expr: str | None = None
    predicate: Predicate | None = None

    def matches(self, item: MediaItem) -> bool:
        return True if self.predicate is None else self.predicate(item)


def compile_filter(text: str | None) -> MediaFilter:
    if text is None or not text.strip():
        return MediaFilter()
    tokens = _tokenize(text)
    if not tokens:
        return MediaFilter()
    return MediaFilter(expr=text.strip(), predicate=_Parser(tokens).parse())


def validate_spec(spec: TaskSpec) -> None:
    for value in (spec.id_from, spec.id_to):
        if value is not None and not MIN_MESSAGE_ID <= value <= MAX_MESSAGE_ID:
            raise FilterError(f"消息序号必须在 {MIN_MESSAGE_ID} 到 {MAX_MESSAGE_ID} 之间")
    if spec.id_from is not None and spec.id_to is not None and spec.id_from > spec.id_to:
        raise FilterError("起始序号不能大于结束序号")
    compile_filter(spec.filter_expr)


def build_filter(spec: TaskSpec) -> MediaFilter:
    return compile_filter(spec.filter_expr)
