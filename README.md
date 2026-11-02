# task-mailbox-mcp

部署在 VPS 的持久任务信箱 MCP，供云端 Dot 与 Mac 上的 Codex／Kimi 客户端通过同一个 OAuth HTTPS 端点收发任务和小结果。任务、结果版本与事件存入 VPS 本地 SQLite；请求超时后可以按原 `job_id` 查询，重复传输沿稳定 ID 去重。

```text
云端 Dot ── OAuth HTTPS MCP ── VPS 持久信箱 ── OAuth HTTPS MCP ── Mac Codex／Kimi
                                                        主动读取、按原授权承接
```

本服务只收发和保存数据。配置客户端后，仍需客户端主动调用工具；没有本地常驻桥、后台拉取、自动执行、自动唤醒或新的 AI 协调层。Mac 的 Herdr 控制与项目执行沿既有入口，信箱提交、结果报告和读取确认分别留证。

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

默认每份任务或结果最大 64 KiB、SQLite 主数据库最大 256 MiB、最多保存 10000 个任务，均可配置。任务条数满额后拒绝新 ID，原 ID 仍可对账；数据库写满时明确失败，不删旧数据、不自动清理或扩额。256 MiB 只限制主数据库，rollback journal、OAuth 状态、日志和依赖另外占用空间。

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

VPS 已有运行时版本回读：Python 3.14.4、FastMCP 4.0.11、MCP 2.3.0、uvicorn 0.54.0。版本信息不代表新服务已部署。现有 Gitee 回环端口 `8765` 保留；本服务候选回环端口为 `8766`，候选外部路径为 `/mailbox/mcp`，路由与认证仍须实际集成验证。

systemd 模板设置 `MemoryHigh=192M`、`MemoryMax=256M`、`CPUQuota=50%`、`TasksMax=32`、`LimitNOFILE=256`，作为保护上限；生产占用、响应时间及长期容量尚未验证。

启动所缺的是新服务的独立 OAuth 身份配置。公子先创建独立 GitHub OAuth App，再在 VPS 本地 TTY 运行 [configure-private.py](deployment/configure-private.py)，通过隐藏输入填写新 App secret；助手不需要您在聊天发送凭据。辅助脚本保留本服务已有签名密钥，只写权限 `0600` 的私密环境文件，不启动服务。完成该配置后才能继续启动、HTTPS 路由与云端登记，具体见[部署步骤](docs/deployment.md#3-独立-oauth-与持久认证状态)。

目标 Linux／Python 3.14.4 环境的完整依赖见 [requirements.lock.txt](requirements.lock.txt)。宿主准备脚本按锁定文件安装并禁用 pip 缓存；其他开发平台须另核兼容性。

## 当前验证

Store 的 17 项标准库行为测试已通过；扩大默认值前，VPS 临时回环 MCP 的 9 工具／两个模拟客户端／账号拒绝／OAuth 元数据验证已通过。当前约 240 KiB UTF-8 传输重跑在 SSH 连接阶段超时，尚未执行。新独立环境的该测试进程峰值 RSS 为 121444 KiB（约 118.6 MiB）；不是生产容量结论。VPS 独立新虚拟环境已创建，占用 125212 KiB（约 122.3 MiB），主执行侧已在此独立环境复跑 MCP 与 OAuth 存储测试，均通过。

VPS 常驻新服务、HTTPS、生产 OAuth、云端登记、本地发现、真实双方 nonce 闭环及 Herdr 执行均为 `NOT_RUN`，详见唯一当前验证入口 [docs/verification.md](docs/verification.md)。这些准备与测试不构成部署或长期稳定性验收。

本仓库公开。只提交源码、依赖声明、文档及占位配置；令牌、私钥、OAuth 密钥／状态、数据库、真实连接配置和原始宿主输出保存在各自受限运行目录。
