"""Bot 命令文本解析：把 /dl ... 转成 TaskSpec，不涉及网络。"""

from __future__ import annotations

import argparse
import re
import shlex
from typing import NoReturn

from tgdl.filters import FilterError, parse_datetime, validate_spec
from tgdl.link_parser import LinkParseError, parse_link
from tgdl.models import MediaKind, TaskSpec

HELP_TEXT = """📖 用法

/dl <链接> [选项]
  --regex <表达式>   按正则过滤（匹配消息文字或文件名，忽略大小写）
                    表达式以 - 开头时须写成 --regex=<表达式>
  --from <日期>      起始时间，如 2026-01-01 或 2026-01-01T12:00
  --to <日期>        结束时间（含当天）
  --ids <起始>-<结束> 消息序号范围，如 --ids 100-500（与 --from/--to 互斥）
  --type video|photo|all  媒体类型，默认 all

/tasks            查看排队中和进行中的任务
/status           当前任务实时进度
/cancel <任务ID>   取消任务
/help             显示本帮助

示例：
/dl https://t.me/somechannel --regex "4K" --from 2026-01-01 --to 2026-03-01
/dl https://t.me/c/1234567890/50 --ids 50-200 --type video"""


class CommandError(ValueError):
    """命令格式或参数错误，消息可直接回复给用户。"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CommandError(message)


def _build_dl_parser() -> _Parser:
    parser = _Parser(prog="/dl", add_help=False, exit_on_error=False, allow_abbrev=False)
    parser.add_argument("link")
    parser.add_argument("--regex")
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    parser.add_argument("--ids")
    parser.add_argument("--type", dest="kind", choices=[k.value for k in MediaKind], default=MediaKind.ALL.value)
    return parser


_DL_PARSER = _build_dl_parser()
_IDS = re.compile(r"^([0-9]{1,10})-([0-9]{1,10})$")
_TASK_ID = re.compile(r"#?([0-9]{1,10})")


def split_command(text: str) -> tuple[str, list[str]]:
    """返回 (命令名, 参数列表)。命令名统一小写并去掉 @botname 后缀。"""
    # 不把反斜杠当转义符，保证 --regex ep\d+ 这类未加引号的正则原样保留
    lexer = shlex.shlex(text.strip(), posix=True)
    lexer.whitespace_split = True
    lexer.escape = ""
    # shlex.shlex 默认把 # 当注释起始符，会把 "/cancel #3" 或 "ep#1" 里 # 之后的内容丢掉
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise CommandError(f"参数引号不匹配: {exc}") from exc
    if not tokens or not tokens[0].startswith("/"):
        raise CommandError("不是有效命令")
    name = tokens[0].split("@", 1)[0].lower()
    return name, tokens[1:]


def parse_ids(text: str | None) -> tuple[int | None, int | None]:
    if text is None:
        return None, None
    match = _IDS.match(text.strip())
    if not match:
        raise CommandError("--ids 格式应为 起始-结束，例如 --ids 100-500")
    return int(match.group(1)), int(match.group(2))


def _parse_dl_args(args: list[str]) -> argparse.Namespace:
    # exit_on_error=False 下 argparse.ArgumentError 不经过 error()，需在此统一转成 CommandError
    try:
        return _DL_PARSER.parse_args(args)
    except argparse.ArgumentError as exc:
        raise CommandError(str(exc)) from exc


def parse_dl(args: list[str]) -> TaskSpec:
    ns = _parse_dl_args(args)
    try:
        id_from, id_to = parse_ids(ns.ids)
        spec = TaskSpec(
            link=parse_link(ns.link),
            raw_link=ns.link,
            regex=ns.regex or None,
            date_from=parse_datetime(ns.date_from) if ns.date_from else None,
            date_to=parse_datetime(ns.date_to, end_of_day=True) if ns.date_to else None,
            id_from=id_from,
            id_to=id_to,
            kind=MediaKind(ns.kind),
        )
        validate_spec(spec)
    except (LinkParseError, FilterError) as exc:
        raise CommandError(str(exc)) from exc
    return spec


def parse_cancel(args: list[str]) -> int:
    match = _TASK_ID.fullmatch(args[0]) if len(args) == 1 else None
    if not match:
        raise CommandError("用法: /cancel <任务ID>")
    return int(match.group(1))
