"""Bot 命令文本解析：语法与 telegram_media_downloader 的机器人保持一致。"""

from __future__ import annotations

import re
from dataclasses import replace

from tgdl.filters import FilterError, validate_spec
from tgdl.link_parser import LinkParseError, parse_link
from tgdl.models import TaskSpec

HELP_TEXT = """📖 用法

/download <链接> [<起始ID> <结束ID>] [过滤表达式]
  起始ID 为 1 表示从头开始，结束ID 为 0 表示到最后一条
  只给消息链接、不给序号：只下载该条消息（属于相册则整个相册）
  /dl 是 /download 的别名；直接发送一条消息链接也会下载该条消息

过滤表达式（可用 and / or / && / || 与括号组合）：
  字段：id  caption  file_name  file_size  media_type(video/photo)
        file_extension  date
  比较：>  <  >=  <=  ==  !=
  字符串加引号；r'...' 表示正则，需整体匹配，通常写成 r'.*关键词.*'
  大小可带单位 KB / MB / GB；日期如 2026-05-10、2026.05.10 14:30、2026-05

/tasks            显示看板（当前进度、队列、历史），自动刷新
/cancel <任务ID>   取消任务
/help             显示本帮助

示例：
/download https://t.me/somechannel 1 0
/download https://t.me/somechannel 100 200 media_type == 'video'
/download https://t.me/c/1234567890/50 1 0 caption == r'.*#饼干姐姐.*' and date >= 2026-05-10
https://t.me/somechannel/123"""

LINK_PREFIX = "https://t.me/"
RANGE_HINT = "起始ID 与 结束ID 需同时给出，例如 1 0（0 表示到最后一条）"


class CommandError(ValueError):
    """命令格式或参数错误，消息可直接回复给用户。"""


_TASK_ID = re.compile(r"#?([0-9]{1,10})")
_INT = re.compile(r"[0-9]{1,10}")


def normalize_dashes(text: str) -> str:
    """Telegram 客户端会把连续两个 - 自动替换成长破折号，这里还原。"""
    return text.replace("—", "--").replace("–", "--")


def split_command(text: str) -> tuple[str, str]:
    """返回 (命令名, 其余原文)。命令名统一小写并去掉 @botname 后缀；纯消息链接视为 /download。"""
    cleaned = normalize_dashes(text.strip())
    if cleaned.startswith(LINK_PREFIX):
        return "/download", cleaned
    parts = cleaned.split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        raise CommandError("不是有效命令")
    name = parts[0].split("@", 1)[0].lower()
    return name, parts[1] if len(parts) > 1 else ""


def _split_range(rest: str) -> tuple[int | None, int | None, str]:
    """从链接之后的文本里取出 <起始ID> <结束ID>，剩余部分是过滤表达式。"""
    parts = rest.split(maxsplit=2)
    if not parts or not _INT.fullmatch(parts[0]):
        return None, None, rest
    if len(parts) < 2 or not _INT.fullmatch(parts[1]):
        raise CommandError(RANGE_HINT)
    start, end = int(parts[0]), int(parts[1])
    if start < 1:
        raise CommandError("起始ID 必须 ≥ 1")
    return start, (None if end == 0 else end), parts[2] if len(parts) > 2 else ""


def parse_download(rest: str) -> TaskSpec:
    parts = rest.strip().split(maxsplit=1)
    if not parts:
        raise CommandError("用法: /download <链接> [<起始ID> <结束ID>] [过滤表达式]")
    link_text, tail = parts[0], parts[1] if len(parts) > 1 else ""
    try:
        link = parse_link(link_text)
        id_from, id_to, filter_expr = _split_range(tail)
        spec = TaskSpec(link=link, raw_link=link_text, id_from=id_from, id_to=id_to, filter_expr=filter_expr or None)
        if spec.filter_expr and id_from is None and link.message_id is not None:
            spec = replace(spec, id_from=link.message_id)  # 消息链接 + 过滤条件：从该条开始筛选而不是只下这一条
        validate_spec(spec)
    except (LinkParseError, FilterError) as exc:
        raise CommandError(str(exc)) from exc
    return spec


def parse_cancel(rest: str) -> int:
    match = _TASK_ID.fullmatch(rest.strip())
    if not match:
        raise CommandError("用法: /cancel <任务ID>")
    return int(match.group(1))
