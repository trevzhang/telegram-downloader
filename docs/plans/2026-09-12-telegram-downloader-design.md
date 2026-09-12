# Telegram 频道/群组媒体下载器 设计文档

日期：2026-09-12
状态：已实现（2026-09-12），后续变更以仓库代码为准

## 1. 目标

基于 Telegram 实现一个视频、图片自动下载器：

1. 给定 Telegram 频道或群组链接（t.me 复制链接），下载其中的视频和图片。
2. 支持按正则过滤内容，支持按时间范围或消息序号范围过滤。
3. 支持 SOCKS5 代理，满足中国大陆使用。
4. 任务执行期间通过 Bot 消息实时更新任务列表、进度、速度、预计完成时间。

## 2. 关键约束与决策

| 决策 | 结论 | 原因 |
|---|---|---|
| 账号架构 | 用户账号 (MTProto) 负责读取和下载，Bot 负责命令交互和进度推送 | Bot API 无法读取历史消息，且 getFile 限制 20MB |
| 技术栈 | Python 3.11+，Telethon，asyncio 单进程 | MTProto 生态最成熟，原生支持代理和进度回调 |
| 交互方式 | 单条命令带参数 | 便于复用和复制粘贴 |
| 代理 | SOCKS5 本地端口 | 匹配 Clash/V2Ray 等常见本地代理 |
| 运行环境 | 本地 Mac 直接运行 | 最简部署 |
| 存储结构 | `downloads/<频道>/<YYYY-MM>/<消息ID>_<文件名>` | 按时间浏览方便，消息 ID 保证唯一有序 |
| 并发模型 | 任务串行排队，任务内多文件并行（默认 3） | 进度清晰，不易触发 FloodWait |
| 去重续传 | 已存在且大小一致则跳过，未完成的 .part 重新下载 | 实现简单可靠 |
| 媒体类型 | 原生 video/photo，以及 MIME 为 video/* 或 image/* 的 document | 高清视频常以文件方式发送 |
| 正则目标 | 消息 caption 与文件名任一匹配 | 覆盖两种常见标题位置 |

## 3. 整体架构

```
┌──────────────┐  命令   ┌─────────────┐  TaskSpec  ┌────────────┐
│  Telegram 用户│ ─────▶ │ bot_client  │ ─────────▶ │ task_queue │
│  (OWNER_ID)  │ ◀───── │ (Telethon)  │ ◀───────── │  (串行)     │
└──────────────┘  进度   └─────────────┘  进度事件   └─────┬──────┘
                                                          │
                                        ┌─────────────────┴──────────────┐
                                        │  scanner ──▶ downloader (x3)   │
                                        │        user_client (Telethon)  │
                                        └────────────────┬───────────────┘
                                                         │ SOCKS5
                                                         ▼
                                                    Telegram DC
```

### 两个 Telethon 客户端

- **user_client**：用个人账号登录。首次运行在终端交互输入手机号和验证码，session 持久化到 `data/user.session`。负责解析链接、遍历历史、下载媒体。
- **bot_client**：用 Bot Token 登录。只响应 `OWNER_ID` 指定的用户，其余消息一律忽略，防止陌生人驱动个人账号下载。

两个客户端共用同一份代理配置。未配置代理时直连。

### 模块划分（每文件不超过 300 行）

| 模块 | 职责 |
|---|---|
| `config.py` | 用 pydantic-settings 加载 `.env`，启动时校验必填项 |
| `models.py` | `TaskSpec`、`MediaItem`、`TaskState` 等 frozen dataclass |
| `link_parser.py` | 解析 t.me 链接为 (频道标识, 可选消息 ID, 是否邀请链接) |
| `filters.py` | 正则、时间范围、序号范围过滤器及互斥校验 |
| `scanner.py` | 用 user_client 迭代消息，产出 `MediaItem` 列表 |
| `downloader.py` | 信号量控制并发，下载单文件，写 .part 后原子重命名，重试 |
| `task_queue.py` | 串行任务队列、状态机、取消标记 |
| `progress.py` | `ProgressSnapshot`/`ProgressTracker`：汇总字节数，滑动窗口速度，ETA，节流编辑 Bot 消息 |
| `bot/commands.py` | 命令行参数解析（argparse 风格） |
| `bot/handlers.py` | 命令处理与回复 |
| `main.py` | 装配、登录、启动事件循环 |

## 4. 命令设计

```
/dl <链接> [--regex <表达式>] [--from <日期>] [--to <日期>] [--ids <起始>-<结束>] [--type video|photo|all]
/tasks              查看排队中和进行中的任务
/cancel <任务ID>     取消排队或进行中的任务
/status             当前任务实时详情
/help               用法说明
```

### 链接支持

- `https://t.me/channel` 公开频道或群组
- `https://t.me/channel/123` 从消息 123 开始
- `https://t.me/c/1234567890/123` 私有群组（需已加入）
- `https://t.me/+inviteHash` 邀请链接，未加入时自动加入

### 过滤规则

- `--from` / `--to` 与 `--ids` 互斥，同时出现则报错。
- 日期接受 `2026-01-01` 或 `2026-01-01T12:00`，按消息发送时间过滤，闭区间。
- `--ids` 为消息 ID 闭区间。
- 正则默认忽略大小写，无效表达式在入队前拒绝。
- 相册中每个媒体单独处理，共享同一 caption。
- 时间和序号范围下推到 Telethon 的 `iter_messages(offset_date / min_id / max_id)`，避免全量遍历；正则在本地匹配。

## 5. 数据流

1. handler 解析命令，构造不可变 `TaskSpec`，入队，回复「已加入队列，任务 #N」。
2. 队列 worker 取出任务，scanner 迭代消息，产出 `MediaItem`（消息 ID、日期、文件名、大小、类型、caption）。
3. Bot 发送「任务概览」：共 N 个文件，总大小 X GB。
4. downloader 以 `asyncio.Semaphore(3)` 并发下载到目标路径，先写 `.part` 再原子重命名。目标已存在且大小一致则跳过。
5. progress 每 5 秒编辑一次进度消息；完成后编辑为最终汇总。

## 6. 进度消息

```
📥 任务 #3  @channel_name
状态：下载中  12/48 个文件
进度：▓▓▓▓▓▓░░░░░░░░░ 41.2%  (1.8 GB / 4.4 GB)
速度：6.3 MB/s    剩余：约 7 分钟
正在下载：
  • 1234_video_a.mp4  78%
  • 1240_video_b.mp4  35%
  • 1251_photo.jpg    12%
已跳过：3 个（已存在）  失败：0 个
```

- 单条消息节流编辑，间隔 5 秒，避免编辑频率限制。
- 速度用最近 10 秒滑动窗口计算。
- ETA = 剩余字节 / 窗口平均速度。
- 完成后列出失败文件及原因。

## 7. 错误处理

| 场景 | 处理 |
|---|---|
| FloodWaitError | 按指示秒数等待后重试，进度消息显示「限流等待 N 秒」 |
| 单文件网络错误 | 重试 3 次，指数退避；仍失败记入失败列表，不影响其他文件 |
| 频道无法访问 | 立即回复具体原因（不存在、需邀请、已封禁） |
| 参数错误 | 入队前校验，回复用法提示 |
| 进程中断 | 启动时清理残留 .part；任务队列不持久化，重启后重新下发命令 |
| 取消 | 设置取消标记，下载协程收到 CancelledError 后清理 .part |

日志写到 `data/logs/`，按天轮转，记录每个文件的下载结果。

## 8. 测试策略

目标覆盖率 80%+，使用 pytest 与 pytest-asyncio，遵循 TDD。

- **单元测试**：`link_parser`、`filters`、`progress`、`bot/commands`。纯函数，不依赖网络。
- **集成测试**：`scanner`、`downloader`、`task_queue` 使用 `tests/fakes/` 中的假 Telethon client，验证并发、跳过、.part 重命名、重试、串行与取消。
- **端到端**：`scripts/e2e_smoke.py` 手动对真实测试频道跑小任务，不进 CI。

## 9. 项目结构

```
telegram-downloader/
├── pyproject.toml          # uv 管理，依赖 telethon python-socks pydantic-settings
├── .env.example            # API_ID API_HASH BOT_TOKEN OWNER_ID PROXY_HOST PROXY_PORT DOWNLOAD_DIR CONCURRENCY
├── README.md
├── docs/plans/
├── src/tgdl/
│   ├── main.py  config.py  models.py
│   ├── link_parser.py  filters.py  scanner.py
│   ├── downloader.py  task_queue.py  progress.py
│   └── bot/commands.py  bot/handlers.py
├── tests/
│   ├── unit/  integration/  fakes/
├── scripts/e2e_smoke.py
└── data/                   # session、logs，已 gitignore
```

## 10. 编码约束

- 所有状态对象为 frozen dataclass，更新通过 `dataclasses.replace` 生成新对象。
- secrets 仅从 `.env` 读取，启动时校验缺失即退出。
- 单文件不超过 300 行，函数不超过 50 行。
- 所有外部输入（命令参数、链接、消息对象字段）在边界处校验。

## 11. 明确不做 (YAGNI)

- 任务队列持久化与重启恢复
- 真正的字节级断点续传
- 多任务并行
- HTTP / MTProxy 代理
- Docker 部署
- 对话式向导
