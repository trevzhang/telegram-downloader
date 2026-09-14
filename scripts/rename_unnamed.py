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
from tgdl.main import build_user_client
from tgdl.paths import channel_dir_name, file_name_for
from tgdl.scanner import extract_media, resolve_channel

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
        renamed = 0
        for message in await client.get_messages(entity, ids=sorted(targets)):
            item = extract_media(message) if message is not None else None
            if item is None or not item.caption:
                continue
            src = targets[item.message_id]
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
