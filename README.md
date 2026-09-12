# tgdl — Telegram 频道/群组视频图片下载器

**架构一句话：** 单进程 asyncio，两个 Telethon 客户端——**用户账号**负责解析链接、扫描消息、下载媒体；**Bot** 负责接收 OWNER 的命令并实时编辑进度消息。任务串行排队，任务内文件按信号量并发下载。

## 目录

- [准备工作](#准备工作)
- [安装](#安装)
- [首次登录](#首次登录)
- [命令用法](#命令用法)
- [文件存放结构](#文件存放结构)
- [下载到 NAS](#下载到-nas)
- [行为说明](#行为说明)
- [常见问题](#常见问题)
- [首次联调检查清单](#首次联调检查清单)
- [开发](#开发)

## 准备工作

1. **申请 api_id / api_hash**：登录 <https://my.telegram.org> → API development tools，创建一个应用，记下 `api_id` 和 `api_hash`。
2. **创建 Bot**：在 Telegram 里找 [@BotFather](https://t.me/BotFather)，发送 `/newbot`，按提示取名，记下返回的 Bot Token（形如 `123456:ABC-DEF...`）。
3. **查询自己的用户 ID**：给 [@userinfobot](https://t.me/userinfobot) 发任意消息，它会回复你的数字 ID。这个 ID 将作为 `OWNER_ID`，**只有这个用户能操作 Bot**。
4. **本地 SOCKS5 代理**（如果所在网络无法直连 Telegram）：确认代理已在本机监听，例如 `127.0.0.1:7890`。
5. **给 Bot 发一条消息**：在 Telegram 里打开你的 Bot 并点 Start（或发 `/start`），否则 Bot 无法主动给你推送启动通知。

## 安装

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync                    # 安装运行与开发依赖
cp .env.example .env       # 复制配置模板
```

请在**项目根目录**执行以上命令：`.env`、`data/`、`downloads/` 都相对当前工作目录解析。依赖中包含 `cryptg`（原生 AES），没有它 Telethon 会退回纯 Python 加解密，下载时会阻塞事件循环、明显变慢。

编辑 `.env`，填写必填项与代理：

| 变量 | 必填 | 说明 |
|---|---|---|
| `API_ID` / `API_HASH` | 是 | 来自 my.telegram.org |
| `BOT_TOKEN` | 是 | 来自 @BotFather |
| `OWNER_ID` | 是 | 你自己的用户 ID，Bot 只响应此用户 |
| `PROXY_HOST` / `PROXY_PORT` | 否 | SOCKS5 代理；**必须同时设置或同时留空** |
| `PROXY_USERNAME` / `PROXY_PASSWORD` | 否 | 代理认证，无则留空 |
| `DOWNLOAD_DIR` | 否 | 下载根目录，默认 `downloads`；指向 NAS 时用挂载后的路径，见[下载到 NAS](#下载到-nas) |
| `DATA_DIR` | 否 | session、日志存放目录，默认 `data` |
| `CONCURRENCY` | 否 | 任务内并发下载文件数（1-10），默认 3 |
| `PROGRESS_INTERVAL` | 否 | 进度消息编辑间隔（秒，≥1），默认 5 |
| `MAX_RETRIES` | 否 | 单文件网络错误重试次数（0-10），默认 3 |

配置只从环境变量或 `.env` 读取，源码中不含任何密钥；`.env`、`data/`、`downloads/` 已在 `.gitignore` 中。

## 首次登录

```bash
uv run tgdl
```

请在项目根目录执行。首次运行会在终端提示输入**手机号**（国际格式，如 `+8613800000000`）和 Telegram 发来的**验证码**；开启了两步验证的账号还会要求输入密码，因此首次登录必须在交互终端运行（非 TTY 环境会直接报错提示）。**注意：手机号那一步必须输入你自己的个人账号手机号，千万不要输入 Bot Token。** Bot 账号无法读取频道历史；程序会在登录后校验，若发现 `data/user.session` 登录的是 Bot，会报错并要求删除该文件后重新登录。登录成功后 session 持久化在 `data/user.session` 与 `data/bot.session`，之后启动免登录。若更换了 `BOT_TOKEN`，请删除旧的 `data/bot.session` 再启动，否则会提示该 session 属于另一个 Bot。

启动成功后 Bot 会给你发一条「✅ tgdl 已启动，发送 /help 查看用法」；如果你还没给 Bot 发过 `/start`，这条通知会发送失败，程序只记录警告并继续运行。日志同时输出到控制台与 `data/logs/tgdl.log`（按天轮转，保留 14 天）。按 `Ctrl+C` 退出。

## 命令用法

在与 Bot 的**私聊**中发送命令（Bot 只响应 OWNER 的私聊，群组里的命令会被忽略）：

```text
📖 用法

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
/dl https://t.me/c/1234567890/50 --ids 50-200 --type video
```

支持的链接形式：

| 形式 | 示例 | 说明 |
|---|---|---|
| 公开频道/群组 | `https://t.me/somechannel`、`@somechannel` | 也可带消息序号 `https://t.me/somechannel/123`，表示从该消息开始 |
| 私有频道消息链接 | `https://t.me/c/1234567890/50` | 从「复制消息链接」得到，账号须已是成员；同样从 `<msg>`（这里是 50）开始 |
| 邀请链接 | `https://t.me/+AbCdEf123`、`https://t.me/joinchat/AbCdEf123` | 用户账号会自动尝试加入 |

更多示例：

```text
/dl https://t.me/somechannel                       # 下载全部视频与图片
/dl https://t.me/somechannel --type photo          # 只要图片
/dl https://t.me/somechannel --ids 1-50            # 只下载消息序号 1 到 50
/dl https://t.me/somechannel --regex "ep\d+"       # 正则可不加引号，反斜杠原样保留
/dl https://t.me/somechannel --regex=-hidden       # 正则以 - 开头时必须用 = 连接
/cancel 3                                          # 也可写作 /cancel #3
```

## 文件存放结构

```text
downloads/<频道>/<YYYY-MM>/<消息ID>_<文件名>
```

- `<频道>`：优先用频道 username，其次标题，最后是数字 ID；非法字符替换为 `_`。
- `<YYYY-MM>`：按消息发布月份分目录。
- `<消息ID>_<文件名>`：消息序号加原始文件名，同一频道内不会重名；无文件名的媒体用 `video.mp4` / `photo.jpg` 之类的默认名。
- 下载过程中写入 `<文件名>.part`，完成后原子重命名为最终文件名。

## 下载到 NAS

程序只把文件写到 `DOWNLOAD_DIR` 指向的本地路径，所以把 NAS 共享目录挂载到 Mac 上、再把 `DOWNLOAD_DIR` 指向挂载路径即可，不需要改代码。以群晖为例，NAS 上的 `/volume1/Porn` 在 Mac 上挂载后通常是 `/Volumes/Porn`（共享文件夹名即挂载点名）。

1. **挂载共享目录**：Finder → 前往 → 连接服务器，输入 `smb://<NAS 地址>/<共享文件夹名>`，用 NAS 账号登录并勾选「在钥匙串中记住密码」。挂载成功后 `/Volumes/<共享文件夹名>` 就会出现。命令行等价做法：

   ```bash
   open "smb://<NAS 地址>/<共享文件夹名>"
   ```

2. **让挂载在登录时自动恢复**：系统设置 → 通用 → 登录项，把挂载后出现在桌面上的那个网络卷拖进去。
3. **修改 `.env`**：

   ```dotenv
   DOWNLOAD_DIR=/Volumes/<共享文件夹名>/<子目录>
   ```

   路径必须是挂载后的 Mac 路径，不是 NAS 内部的 `/volume1/...`。

**掉线保护**：下载根目录必须已经存在，程序不会自动创建它。任务开始前会检查，若目录不存在（NAS 未挂载或已掉线）任务会直接失败并提示「下载目录不存在（NAS 未挂载？）」，而不是把文件悄悄写到本机磁盘上的同名目录里。重新挂载后再发一次 `/dl` 即可。启动时也会对不存在的下载目录打一条警告日志。

**性能提示**：文件先写成 `.part` 再在同一目录内重命名，SMB 上这一步是原子的；`.part` 清理在启动时对整个下载根目录做递归扫描，若共享目录里文件很多，建议 `DOWNLOAD_DIR` 指向专用子目录而不是整个共享根。

## 行为说明

- **跳过已存在文件**：目标文件已存在且大小一致时直接标记「跳过」，不重复下载，因此可以放心重复执行同一命令来补齐。
- **`.part` 清理**：单文件失败或任务取消时会删除对应 `.part`；程序启动时也会清理下载目录内所有残留 `.part`。
- **串行队列**：任务按提交顺序一个接一个执行，`/tasks` 可看排队情况，`/cancel <ID>` 可取消排队中或进行中的任务。
- **任务内并发**：一个任务内的文件按 `CONCURRENCY` 并发下载（默认 3）。数值越大越容易触发 Telegram 限流，建议 3-5。
- **限流（FloodWait）处理**：用户客户端关闭了 Telethon 的自动等待。下载阶段遇到 `FloodWaitError` / `FloodPremiumWaitError` 时会在进度消息里显示「限流等待 N 秒」，等待后自动重试，且不占用重试次数；解析链接 / 扫描阶段遇到限流时 Bot 会发「⏳ 任务 #N 限流，等待 N 秒后重试」，等待后同样自动重试。两个阶段累计等待上限均为 1 小时（3600 秒），超过则单个文件 / 整个任务标记失败。
- **时间过滤与时区**：`--from/--to` 不带时区时按**进程本地时区**理解，`--to 2026-01-31` 含当天 23:59:59。通过环境变量 `TZ` 可指定时区，例如 `TZ=Asia/Shanghai uv run tgdl`。
- **代理**：`PROXY_HOST` 与 `PROXY_PORT` 必须同时设置或同时留空，否则启动时报配置错误。两个客户端共用同一个代理。
- **权限**：Bot 只响应 `OWNER_ID` 在私聊中发来的以 `/` 开头的消息；其他人或群组内的消息一律忽略。
- **不可变状态**：所有任务/文件状态都是 frozen dataclass，进度更新通过 `dataclasses.replace` 生成新对象。

## 常见问题

### 代理不通 / 一直连不上

先在命令行验证代理本身可用：

```bash
curl -x socks5h://127.0.0.1:7890 https://api.telegram.org -I
```

返回 `HTTP/2 200` 或 `302` 即正常；超时或拒绝连接说明代理没起来或端口不对。再检查 `.env` 里的 `PROXY_HOST`/`PROXY_PORT` 是否与代理一致，以及是否漏填了其中一个。

### 提示限流（FloodWait）

Telegram 对下载速率有限制，短时间内大量下载会被要求等待几十秒到几小时。无论发生在下载阶段还是解析 / 扫描阶段，程序都会自动等待并重试，不需要干预（扫描阶段会收到一条「⏳ 限流」通知）；累计等待超过 1 小时才会放弃并标记失败，稍后重新提交即可。如果频繁出现，把 `CONCURRENCY` 调小或分批下载（用 `--ids` 缩小范围）。非会员账号在下载大文件时收到的 `FloodPremiumWaitError` 同样按限流等待处理。

### 「频道为私有或已被封禁，当前账号无权访问」/「无法解析频道，请确认账号已加入该频道」

`https://t.me/c/<id>/<msg>` 这类链接要求登录的**用户账号已经是该频道成员**；Bot 本身没有读取频道的权限，也不能替代用户账号。新登录的 session 实体缓存为空，程序会自动拉取一次会话列表预热缓存后重试；如果仍然提示无法解析，请先在 Telegram 客户端用同一账号加入（或打开过）该频道，再重试。注意 `t.me/c/<id>/<msg>` 也会从 `<msg>` 这条消息开始扫描，想下载整个频道请把 `/<msg>` 去掉。

### 邀请链接「已发送加入申请，等待管理员审批后重试」

该频道开启了入群审批，程序已代你提交申请。等管理员通过后再发一次相同的 `/dl` 命令即可。若提示「邀请链接无效或已过期」，请向频道管理员索要新链接。

### 首次登录卡在输入验证码

验证码会发到你**已登录的其他 Telegram 客户端**（而不是短信）。如果启用了两步验证，还需要输入密码。登录完成后 `data/user.session` 生成，再次启动不再提示。

### Bot 没有回应

- 确认是用 `OWNER_ID` 对应的账号在与 Bot 的**私聊**里发命令。
- 确认命令以 `/` 开头；`/help` 可验证 Bot 是否在线。
- 查看控制台或 `data/logs/tgdl.log` 是否有「收到命令」日志。

## 首次联调检查清单

以下步骤需要真实凭据与网络，请在完成配置后手动逐项验证：

1. 填好 `.env`，用上面的 `curl` 命令确认代理端口可用（返回 200/302）。
2. `uv run tgdl`，完成首次登录，Bot 应发来「✅ tgdl 已启动」。
3. 在 Bot 私聊发送 `/dl https://t.me/<你有权限的公开频道> --ids <小范围>`，观察：概览消息、进度消息每 `PROGRESS_INTERVAL` 秒更新、最终汇总。
4. 发送 `/status`、`/tasks`；任务进行中发送 `/cancel 1`，确认 `downloads/` 下的 `.part` 被清理。
5. 再次发送同一条命令，确认已存在的文件被标记「跳过」。

也可以不经过 Bot、直接在终端对真实频道跑一次小任务：

```bash
uv run python scripts/e2e_smoke.py https://t.me/somechannel --ids 1-5
```

## 开发

```bash
uv run pytest -q --cov               # 运行全部测试并输出覆盖率（要求 ≥ 80%）
uv run pytest -q --cov --cov-report=term-missing   # 显示未覆盖行
uv run ruff check src tests scripts  # 代码检查（ruff format 负责格式化）
uv run mypy src                      # 静态类型检查
```

约定：严格 TDD（先写失败测试再实现）；所有状态对象不可变；单个源文件不超过 300 行、函数不超过 50 行；提交信息格式 `<type>: <中文描述>`。模块依赖关系与实现细节见 `docs/plans/`。
