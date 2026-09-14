"""把下载目录里只剩 `<消息ID>.<扩展名>` 的文件，按当前命名规则（消息文本补位）改名。

用法（需先停止 Bot，脚本要用用户 session）：
    uv run python scripts/rename_unnamed.py <频道链接>
只处理该频道目录下形如 123.mp4 的文件；找不到对应消息或消息没有文本的保持原样。
"""

from __future__ import annotations

import asyncio
import re
import sys

from tgdl.bot.commands import CommandError, parse_download
from tgdl.config import ConfigError, load_settings
from tgdl.filters import MediaFilter
from tgdl.main import build_user_client
from tgdl.models import ChannelRef, TaskSpec
from tgdl.paths import channel_dir_name, file_name_for
from tgdl.scanner import ALBUM_WINDOW, resolve_channel, scan

ID_ONLY = re.compile(r"^(\d+)\.[A-Za-z0-9]+$")


async def rename(link: str) -> int:
    settings = load_settings()
    client = build_user_client(settings)
    await client.start()
    try:
        entity = await resolve_channel(client, parse_download(link).link)
        chan_dir = settings.download_dir / channel_dir_name(entity)
        targets = {int(m.group(1)): f for f in chan_dir.rglob("*") if f.is_file() and (m := ID_ONLY.match(f.name))}
        if not targets:
            print("没有需要改名的文件")
            return 0
        # 用扫描器整段扫描而不是逐条取消息：相册只有第一条带文本，需要传播给同组的其它媒体
        spec = TaskSpec(
            link=ChannelRef(username="x"),
            raw_link=link,
            id_from=max(1, min(targets) - ALBUM_WINDOW),
            id_to=max(targets) + ALBUM_WINDOW,
        )
        items = {i.message_id: i for i in await scan(client, entity, spec, MediaFilter())}
        renamed = 0
        for message_id, src in sorted(targets.items()):
            item = items.get(message_id)
            if item is None or not item.caption:
                continue
            dst = src.with_name(file_name_for(item))
            if dst != src and not dst.exists():
                src.rename(dst)
                renamed += 1
                print(f"{src.name} -> {dst.name}")
        print(f"改名 {renamed} 个，跳过 {len(targets) - renamed} 个")
        return 0
    finally:
        await client.disconnect()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return asyncio.run(rename(argv[0]))
    except (ConfigError, CommandError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
