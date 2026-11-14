# task-mailbox-mcp

本项目唯一维护仓固定为 **[CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp)**，源码、文档、Skill／插件和配置模板在本仓维护，任务在本仓 Issue 与正式 Projects 关联。个人整合仓只保留历史部署副本；后续更新从本仓固定版本交付。

持久任务信箱与协作监控服务。原 MCP 和观测上报运行在 VPS，监控 Web 运行在 Mac 本机。任务、结果版本与观测统一存入 Win PostgreSQL；Mac SQLite 保留本地台账和缓冲。请求超时后按原 `job_id` 查询，重复传输沿稳定 ID 去重。

本机看板入口为 **http://127.0.0.1:8767/mailbox/ui/**，无需 GitHub 登录，只读访问配置中的项目。Web 经独立的 Mac→Win 局域网连接读取同一数据库。VPS 的页面、Web 登录、查询和 SSE 已关闭，原 MCP 与带来源认证的观测上报继续保留。[本机部署与验证](docs/verification/local-monitor.md)

```text
云端 Dot ── OAuth HTTPS MCP ── VPS 持久信箱 ── OAuth HTTPS MCP ── Mac Codex／Kimi
                                                        主动读取、按原授权承接
```

VPS 服务只收发和保存数据。本仓新增 [Mac 协调桥](mac/README.md)，主动拉取事件、保存本机台账、感知配置范围内的 Codex 会话并补传显式结果。桥不自动执行任务，不增加 AI 层，不保证唤醒 Dot；Mac 的 Herdr 控制与项目执行沿既有入口；[Herdr仓Lead显式执行适配器](https://github.com/CF-FE/herdr-4-codexcloud/blob/main/docs/herdr-executor.md)已取得一次Mac合成执行／回传的限定工程验收，自动消费／无人值守派发及Win／Cloud扩展仍未实现或未验。提交、结果报告、读取确认和生产业务验收分别留证。

后续项目协作的统一查看、派发前核读、原仓库资产归属、Iteration 和结果关联，见[Projects 关联契约](docs/projects.md)。正式状态沿原 Projects／任务正文维护；信箱保留固定引用。

重构方案见 [ADR-001：协作监控与数据共享平台](docs/decisions/001-task-collaboration-platform.md)（Accepted，2026-11-14）：统一业务数据库部署到 Win，新增 Web、角色／任务／工作流观测及配套 Skill；人工解阻接口后续启用。本轮不安排数据库备份，实际安装和部署以验证记录为准。

## 组件名称

- **Task Mailbox**：VPS 持久信箱服务及桌面／云端使用的 MCP 插件。
- **Mac 协调桥（Mac Coordination Bridge）**：安装在 Mac 的本地协调组件，负责会话感知、收件台账与结果补传。

## 工具与数据

| 工具 | 行为 |
| --- | --- |
| `submit_job` | 校验内容摘要并持久接收；同 ID 同内容返回原状态，不覆盖冲突内容 |
| `get_job` | 按原任务 ID 查询任务及已报告状态 |
| `list_events` | 按持久游标分页读取事件，可按收件者筛选 |
| `publish_result` | 用稳定事件 ID 保存小结果，产生不可变版本 |
| `get_result` | 读取指定结果版本或最新版本及 SHA-256 |
| `request_cancel` | 保存取消请求，不声明执行已停止 |
| `ack_result_read` | 确认精确版本与摘要，不代替业务验收或写权交还 |
| `whoami` | 返回服务识别的认证主体；任务内的身份文字不能代替认证 |
| `mailbox_usage` | 只读返回记录数、数据库分配页和配置上限，不返回内容或私密路径 |

默认每份任务或结果最大 1 MiB、SQLite 主数据库最大 256 MiB、最多保存 10000 个任务，均可配置。任务条数满额后拒绝新 ID，原 ID 仍可对账；数据库写满时明确失败，不删旧数据、不自动清理或扩额。256 MiB 只限制主数据库，rollback journal、OAuth 状态、日志和依赖另外占用空间。

OAuth 持久值由 `oauth_storage.py` 使用 Fernet 加密，密钥由本服务稳定签名材料派生；专用目录默认采用 8 MiB／512 文件的保守拒写预算，仅适用于单进程唯一写者，不是宿主文件系统硬配额。主进程日志按 1 MiB × 3 文件轮转，约 3 MiB；启动前错误仍进 journald，配置错误退出 78 且不自动重启。应用不启用 HTTP access log。

任务须包含固定 `job_id`、规范化 JSON 的 `payload_hash`，以及版本、来源任务、发件者、收件者、报告路由、显式目标和正文。字段与状态含义见 [SPEC.md](SPEC.md)。`submitted:` 为系统事件 ID 的保留前缀，客户端事件不能使用。

结果状态仅允许 `REPORTED_COMPLETED`、`REPORTED_FAILED`、`UNKNOWN`，表示调用方报告。服务不据此证明模型成功、业务完成或 Herdr 已执行。断线或超时先查原任务，不创建替代执行。

## 开发与部署

Store 测试只需 Python 3.10+ 标准库，在仓库根执行：

```bash
python3 -m unittest discover -s tests -v
```

MCP 运行依赖放入本服务的独立环境，按 [部署说明](docs/deployment.md)配置 OAuth、回环服务和反向代理，再分别登记云端与本地客户端。缺少有效 OAuth 配置时应拒绝启动，不退回公开无认证模式。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tests/smoke_mcp.py
.venv/bin/python tests/smoke_oauth_storage.py
```

MCP 测试使用临时回环端点和测试身份；OAuth 存储测试使用临时目录，均不能证明生产 OAuth。服务入口为 `server.py`；配置模板见 [.env.example](.env.example)、[systemd 单元](deployment/task-mailbox.service)和 [HTTPS 路由示例](deployment/caddy-route.example)。[宿主准备脚本](deployment/prepare-host.sh)只准备账户、独立环境与单元，不启动或公开服务；[客户端指引](clients/README.md)覆盖云端、Codex 与 Kimi。模板中的路径、域名和账户都是示意值，部署前按目标宿主核实。

VPS 运行时为 Python 3.14.4、FastMCP 4.0.11、MCP 2.3.0、uvicorn 0.54.0。本轮信箱已独立启动于回环 `8766`，通过共享 HTTPS 入口的 `/mailbox/` 路由；原 Gitee 回环 `8765` 保留。进程、路由、OAuth、各客户端与双向读取分别记录，当前状态见 [验证记录](docs/verification.md)。

systemd 模板设置 `MemoryHigh=192M`、`MemoryMax=256M`、`CPUQuota=50%`、`TasksMax=32`、`LimitNOFILE=256`，作为保护上限；生产占用、响应时间及长期容量尚未验证。

初次部署需创建独立 GitHub OAuth App，再在 VPS TTY 用 [configure-private.py](deployment/configure-private.py)隐藏输入 secret；不在聊天传递凭据。本轮配置已由公子完成，独立服务与 HTTPS 路由已启动。具体步骤见[部署说明](docs/deployment.md#3-独立-oauth-与持久认证状态)，客户端授权结果以验证记录为准。

目标 Linux／Python 3.14.4 环境的完整依赖见 [requirements.lock.txt](requirements.lock.txt)。宿主准备脚本按锁定文件安装并禁用 pip 缓存；其他开发平台须另核兼容性。

## 当前验证

当前部署、原任务数据核对、真实认证与未验事项以[验证总览](docs/verification.md)为准。本机监控发布版本为 **1.1.0**，此前 **1.0.1** 完成[原 Mac 桥登录恢复](docs/verification/login-recovery-1.0.1.md)；未升级已有运行依赖。下述记录保留早期 SQLite 信箱与 Mac 适配的历史范围。

### 早期验证记录

Store 的 17 项标准库行为测试已通过；扩大默认值前，VPS 临时回环 MCP 的 9 工具／两个模拟客户端／账号拒绝／OAuth 元数据验证已通过。当前约 240 KiB UTF-8 传输重跑发生 SSH／观察超时，未取得完整回执，保留 UNKNOWN。新独立环境的该测试进程峰值 RSS 为 121444 KiB（约 118.6 MiB）；不是生产容量结论。VPS 独立新虚拟环境已创建，占用 125212 KiB（约 122.3 MiB），主执行侧已在此独立环境复跑 MCP 与 OAuth 存储测试，均通过。

VPS 进程与 HTTPS 已实测。初次独立包被界面标记为“仅限桌面端”，公子已卸载；现改用公子在 ChatGPT 原生注册的 App 连接作为唯一 Task Mailbox 入口。当前桌面会话已发现该云端连接的九项工具，并直接调用 whoami／mailbox_usage 成功；指定 Dot 的只读成功归公子回报；Mac 桥授权、原 nonce 收件回传与当前会话经云端 App 的结果读取已实测，指定 Dot 的结果读取归公子回报且服务精确 ACK 已回读，反方向与业务验收仍分别取证。Mac Codex 原有直接 OAuth 失败与新 App 连接不是同一分支；真实状态见唯一入口 [docs/verification.md](docs/verification.md)。

[Mac 适配实现](mac/README.md)已完成并独立安装：台账、collector、钩子、CLI 与安装器共通过 135 项本地测试（含原服务 17 项）。七类钩子定义已写入，原生信任与实际触发、收件桥授权及长期运行分别验收；当前状态统一见验证记录。[事件与钩子方案](docs/hooks.md)保留完整边界，不授予自动执行权限。

本仓库公开。只提交源码、依赖声明、文档及占位配置；令牌、私钥、OAuth 密钥／状态、数据库、真实连接配置和原始宿主输出保存在各自受限运行目录。

本轮将已验证的 Mac 适配、Projects 关联和当前文档按固定提交完整交接回本仓；初始 `7d162860c10ac2d72719522be7fe01478f4747a3` 保留为历史基线。逐文件来源、排除项与本地适配摘要见 [docs/source-updates.json](docs/source-updates.json)，不建立自动同步或共享运行目录。
