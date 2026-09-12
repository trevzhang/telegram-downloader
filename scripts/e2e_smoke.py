"""手动端到端冒烟：对真实频道跑一次小任务，不进 CI。

用法: uv run python scripts/e2e_smoke.py https://t.me/somechannel --ids 1-5

需要已填好的 .env；首次运行会在终端交互完成用户账号登录。
"""
from __future__ import annotations

import asyncio
import sys

from tgdl.bot.commands import CommandError, parse_dl
from tgdl.config import ConfigError, load_settings
from tgdl.main import build_user_client, build_worker_config
from tgdl.models import TaskSpec, TaskState
from tgdl.worker import TaskWorker

USAGE = "用法: uv run python scripts/e2e_smoke.py <链接> [--ids 起始-结束] [--regex ...] [--type video|photo|all]"
EXIT_USAGE = 2
EXIT_CONFIG = 1
EXIT_INTERRUPTED = 130


class PrintNotifier:
    async def send(self, text: str) -> int:
        print(text)
        return 0

    async def edit(self, message_id: int, text: str) -> None:
        print("\n" + text)


async def run_task(spec: TaskSpec) -> None:
    settings = load_settings()
    user = build_user_client(settings)  # 只登录用户账号，不产生 bot.session 副作用
    await user.start()
    try:
        worker = TaskWorker(user, PrintNotifier(), build_worker_config(settings))
        state = TaskState(task_id=0, spec=spec)
        final = await worker.run(state, lambda s: print(f"[state] {s.status.value}"))
        print(f"结果: {final.status.value}, 文件 {len(final.results)} 个")
    finally:
        await user.disconnect()


def main(args: list[str]) -> int:
    # 先解析参数：用法错误必须在触碰网络/登录之前失败
    try:
        spec = parse_dl(args)
    except CommandError as exc:
        print(f"参数错误: {exc}\n{USAGE}", file=sys.stderr)
        return EXIT_USAGE
    try:
        asyncio.run(run_task(spec))
    except ConfigError as exc:
        print(f"启动失败: {exc}\n请参考 .env.example 创建 .env", file=sys.stderr)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return EXIT_INTERRUPTED
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
