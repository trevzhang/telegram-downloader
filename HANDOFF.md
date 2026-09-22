# HANDOFF — tgdl 会话交接

更新：2026-09-23（最后一次代码提交 a08edfe，2026-09-15）
仓库：https://github.com/trevzhang/telegram-downloader（私有，origin/main，工作树干净）
状态：功能完成并已在真实环境跑通；232 个测试通过，覆盖率 99%，ruff/mypy 零告警。

## 1. 这是什么

基于 Telegram 的频道/群组视频图片下载器。用户账号（Telethon MTProto）负责读取与下载，Bot 负责命令交互与看板；SOCKS5 代理；下载到 NAS。命令语法与过滤表达式刻意对齐 [telegram_media_downloader](https://github.com/tangyoha/telegram_media_downloader)，以便沿用用户已有的目录与文件命名。

技术栈：Python 3.11、uv、Telethon 1.45、regex、pydantic-settings、cryptg。

## 2. 用户环境（重要，影响每个决定）

- 运行方式：Mac 上 Docker Compose（`docker compose up -d`，配置了 `pull_policy: build`，`git pull` 后直接 up 即重建）。此前也在本地用 `uv run tgdl` 跑过。
- 代理：Clash，本地 `127.0.0.1:7890`。容器内由 compose 自动覆盖 `PROXY_HOST=host.docker.internal`，`.env` 无需改。
- 下载目录：群晖 NAS 共享 `/volume1/Porn/Telegram`，Mac 上挂载为 `/Volumes/Porn/Telegram`（`.env` 里 `DOWNLOAD_DIR=/Volumes/Porn/Telegram`）。
- 该目录下有大量旧工具下载的存量文件，命名规则必须兼容（见 §4）。
- 用户 session 与 bot session 在 `data/`（gitignore）。**不要在 Bot 运行时用脚本登录用户 session**，SQLite 会 `database is locked`。

## 3. 关键设计与行为（代码为准）

| 模块 | 职责 |
|---|---|
| `bot/commands.py` | `/download`（别名 `/dl`）`<链接> [起始ID 结束ID] [过滤表达式]`、`/tasks`、`/cancel`、`/help`；裸 `https://t.me/...` 链接也视为下载；`—` 自动还原为 `--` |
| `filters.py` | 旧工具风格过滤表达式解析器：字段 `id/caption/file_name/file_size/media_type/file_extension/date`，`and/or/&&/||`、括号、单位 KB/MB/GB、`r'...'` 正则整体匹配（DOTALL） |
| `scanner.py` | 解析频道（含邀请链接、`t.me/c/` 私有链接，缓存缺失时 `get_dialogs()` 预热）、扫描、相册 caption 传播、单消息模式 |
| `downloader.py` | `iter_download` 分块追加写 `.part`，**断点续传**，已存在且大小一致则跳过，重试/限流/取消 |
| `task_queue.py` | 串行队列，`on_change` 回调唤醒看板 |
| `worker.py` | 单任务流程；只在任务结束时发一条静态汇总；进度通过快照暴露给看板 |
| `bot/dashboard.py` | **唯一一条实时看板消息**，原地编辑；按钮切换「进度/队列/历史」，「刷新」「取消当前」；文字命令后会删旧发新移到底部 |
| `bot/handlers.py` | 只响应 OWNER 私聊；命令与按钮回调分发 |
| `bot/menu.py` | 注册 Bot 命令菜单 |
| `main.py` | 装配、登录校验（用户 session 不能是 Bot）、SIGINT/SIGTERM 两段式关停 |

语义要点：
- 裸消息链接（无序号、无过滤）= 只下该条（含相册）。带序号或过滤时，链接里的消息 ID 只用来定位频道，扫描整个频道（a08edfe 修正）。
- `date <= 2026-09-15` 表示 15 日 0 点之前（与旧工具一致）。
- 频道目录用显示名称；月份目录 `YYYY_MM`。

## 4. 文件命名规则（与 NAS 存量对齐，改动前必须核对）

`<下载根>/<频道显示名>/<YYYY_MM>/<消息ID> - <文件名>`
- Telegram 上没有文件名的媒体：用消息文本（换行折叠为 `_`）作为文件名；再没有文本才是 `<消息ID>.<ext>`。
- 旧工具生成的空文件名形态 `148..mp4` 由 `scripts/rename_unnamed.py` 兼容改名。
- 已对 NAS 做过一轮巡检与迁移（旧 `YYYY-MM` 目录、`<ID>_<名>` 文件），复检为零。
- 旧工具对原生图片用 Pyrogram `file_unique_id` 命名（如 `159 - AgAD….jpg`），Telethon 拿不到，此类图片会重下一次，属已知且接受的差异。

## 5. 运维要点

- 首次登录必须交互式：`docker compose run --rm tgdl`，输入**手机号**（不是 Bot Token），再 `docker compose up -d`。
- 停止：`docker compose stop`（SIGTERM 优雅关停，30 秒宽限）。
- 日志：`data/logs/tgdl.log`，按天轮转 14 天；Telethon 连接被代理回收的告警已过滤。
- 存量改名脚本（需先停 Bot）：`uv run python scripts/rename_unnamed.py https://t.me/c/<id>/1`。
- 下载根目录不存在（NAS 未挂载）时任务直接失败，不会落盘本机。

## 6. 已知未做 / 可选优化

- 每个文件下载前一次 `get_messages`，并发高时易触发限流，限流后并发中的其它 `.part` 保留可续传但会重新走一次重试。
- 旧工具的 `media_width/height/duration` 过滤字段未实现。
- 看板视图状态不持久化，重启后回到「进度」视图。
- `/cancel` 后任务不会自动重新排队，需再发一次命令。
- 未为群晖 Container Manager 做实测；Linux 主机上 `host.docker.internal` 需代理监听非回环地址（Clash `allow-lan`），或改 `network_mode: host`。

## 7. 开发约定

- TDD：先写失败测试再改代码；`uv run pytest -q --cov`（≥80%）、`uv run ruff check src tests scripts`、`uv run mypy src` 三者必须全绿再提交。
- 状态对象一律 frozen dataclass + `replace`；文件 ≤300 行、函数 ≤50 行；常量命名。
- README 中的 `/help` 代码块必须与 `commands.HELP_TEXT` 字节一致（有多次用脚本同步的先例）。
- 提交信息中文，`<type>: <描述>`，历史上无 Co-Authored-By 尾注（用户全局设置已关闭署名）。
- 设计与实现计划在 `docs/plans/`，多个 Task 标题下有「审查后已加固」注记，代码为准。

## 8. 近期改动脉络（供理解决策）

登录用错 Bot Token → 加手机号提示与 session 校验 → NAS 目录与命名对齐旧工具 → 断点续传 → 看板重构（单消息、按钮、视图）→ 命令语法改为旧工具风格（避免 Telegram 把 `--` 变成 `—`）→ 过滤正则 DOTALL、`date` 简写、无文件名用消息文本 → Docker Compose 部署 → 消息链接带过滤时扫描整个频道。
