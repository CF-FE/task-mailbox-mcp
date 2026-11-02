# 部署与接入

目标是在 VPS 运行独立的 task-mailbox MCP，再把同一个 OAuth HTTPS 端点登记到云端和 Mac Codex／Kimi。部署前后的证据只在 [verification.md](verification.md)汇总；本页描述步骤，不表示已经执行。

## 既有环境与本次落点

已回读的 VPS 运行时为 Python 3.14.4、FastMCP 4.0.11、MCP 2.3.0、uvicorn 0.54.0。新服务使用独立源码目录、虚拟环境、状态目录和 systemd 单元，不向既有 Gitee 虚拟环境安装依赖。

| 项目 | 本次约束 |
| --- | --- |
| 既有 Gitee | 保留回环 `8765`、已登记 URL、认证和进程 |
| 新服务监听 | `127.0.0.1:8766`，首次启动前核端口未占用 |
| 外部 MCP 地址 | `https://<PUBLIC_HOST>/mailbox/mcp`；占位主机名，路由已验、受保护工具待验 |
| 公网入口 | 复用获准 HTTPS／反向代理能力，保留既有路径；不直接公开回环服务 |
| 持久数据 | VPS 本机磁盘中的 SQLite、OAuth 状态及稳定密钥；不提交仓库、不放网络共享盘 |
| 客户端 | 云端 Dot、Mac Codex／Kimi 使用同一个对外端点，各自完成 OAuth 授权 |

`<PUBLIC_HOST>`、`<APP_DIR>`、`<STATE_DIR>`、`<ENV_FILE>` 均为示意占位值，不是宿主当前配置。具体环境变量与单元参数以 [.env.example](../.env.example)、[systemd 单元](../deployment/task-mailbox.service)和 [Caddy 路由示例](../deployment/caddy-route.example)为准；这些模板同样不代表已安装配置。

## 1. 只读预检

沿现有获准管理入口核对运行时、可用磁盘、时间同步、目标目录权限、监听端口及 HTTPS 代理配置。只读取所需配置项，不打印环境文件、令牌或私钥；公开报告仅保留脱敏结论。

核实新服务所需回环端口未占用，并定位现有 Gitee 与公网入口的真实管理方式。不要为新服务重启 Gitee、更换其 URL，或启动第二个冲突隧道。现有反向代理变更前保留受限备份，先做配置校验。

## 2. 独立安装与状态

将明确版本的本仓源码部署到独立应用目录，用独立虚拟环境安装本服务依赖。先在该目录执行：

```bash
python3 -m unittest discover -s tests -v
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tests/smoke_mcp.py
.venv/bin/python tests/smoke_oauth_storage.py
```

`smoke_mcp.py` 不接收参数；它使用临时回环端点与 StaticTokenVerifier 测试身份，只验证 MCP 协议与工具收发，不验证生产 OAuth 或真实云端客户端。

`smoke_oauth_storage.py` 使用临时目录验证加密持久化、稳定密钥重开和拒写预算，不读生产 OAuth 数据。VPS 独立新环境现已创建，实际占用 125212 KiB（约 122.3 MiB），主执行侧曾在该环境复跑两项 MCP／OAuth 存储测试通过；后续服务启动与客户端状态见[当前验证记录](verification.md)。

应用进程使用专用低权限运行身份，状态目录只允许该身份及获准管理员访问。SQLite 使用本机持久磁盘、rollback journal 和同步 FULL；备份时使用 SQLite 支持的一致性方式，不把数据库复制到共享目录供多机并发读写。

结果直接存 SQLite，无额外结果文件双写。服务没有自动清理记录、重派任务或外部执行功能；容量不足时应明确写入失败，不能把失败写成持久接收成功。

可采用 [prepare-host.sh](../deployment/prepare-host.sh)准备独立运行账户、源码、虚拟环境和 systemd 单元，并将 [configure-private.py](../deployment/configure-private.py)复制到模板默认应用目录。准备脚本不启动或公开服务、不登记客户端；仅在新服务环境文件尚不存在时生成其独立签名密钥。不要为部署读取、复制或改写用户其他已有密钥；新服务 OAuth 配置按下节由公子在宿主 TTY 填入，登录授权由公子完成。

目标 Linux／Python 3.14.4 部署使用 [完整依赖锁定文件](../requirements.lock.txt)；准备脚本据此安装并禁用 pip 缓存。`requirements.txt` 用于开发环境，不更新既有 Gitee 环境。

## 资源预算与快照

2026-11-02 本轮单次快照：VPS 总 RAM 950.5 MiB、available 658.1 MiB，根盘空闲 13031.3 MiB，1 分钟负载约 0.13。既有 Gitee 虚拟环境约 120.3 MiB，属于已有环境，不是本服务安装量，也不作为可共享运行环境。该快照不能推导持续余量或多客户端容量。

| 配置 | 默认值 | 满额行为或适用范围 |
| --- | --- | --- |
| `TASK_MAILBOX_MAX_PAYLOAD_BYTES` | `1048576` | 单份规范化任务／结果字节数，超额拒绝 |
| `TASK_MAILBOX_MAX_DATABASE_BYTES` | `268435456` | SQLite 主数据库页预算，最小 64 KiB；页数上限使新增写入失败并回滚 |
| `TASK_MAILBOX_MAX_JOBS` | `10000` | 保存的 job 总数，包含已报告结果者；满额拒新 ID，原 ID 可查询／重传 |
| `TASK_MAILBOX_MAX_OAUTH_BYTES` | `8388608` | 专用 OAuth 后端目录的保守拒写字节预算，要求单进程唯一写者 |
| `TASK_MAILBOX_MAX_OAUTH_FILES` | `512` | 专用 OAuth 后端文件预算，不是宿主文件系统硬配额 |

配额可配置，不自动扩额或清理旧数据。已有数据库超过新预算时明确拒绝，不靠删除历史腾空间。数据库写满后，读取已存结果和相同 ID 对账仍是保全路径，但不能假定还能新增结果、事件或读确认。

`mailbox_usage` 只读返回计数及 `page_count`、`page_size`、`allocated_bytes`、配置上限，不泄露内容或数据库路径。256 MiB 不是整个服务目录的总额；rollback journal、OAuth 磁盘状态、日志、源码与依赖另占空间。OAuth 写预算由后端预估余量后保守拒写，不驱逐既有记录；它依赖专用目录与单进程唯一写者，不能当成目录硬配额。OAuth 目录和 journald 仍需独立观察容量与保留策略，长期增长尚未验证。

## 3. 独立 OAuth 与持久认证状态

为本服务建立独立 GitHub OAuth 应用及精确回调地址。`TASK_MAILBOX_PUBLIC_BASE_URL` 使用 `https://<PUBLIC_HOST>/mailbox`，末尾不加 `/mcp`；MCP 端点为 base URL 加 `/mcp`，OAuth 回调为 base URL 加 `/auth/callback`。公共前缀为 `/mailbox`。不能直接复用 Gitee 的 client ID、client secret、回调或认证状态。

初次部署时先在 GitHub 创建独立 OAuth App，填写精确回调，再在 VPS 本地 TTY 使用交互辅助完成配置。本轮公子已完成这项配置；以下是可复用步骤，不是当前待办。按模板默认安装路径，命令为：

```bash
sudo /opt/task-mailbox/.venv/bin/python /opt/task-mailbox/configure-private.py
```

辅助会确认公共 base URL 与允许账号，通过 `getpass` 隐藏输入新 App secret，保留本服务已有签名密钥，并原子写入权限 `0600` 的私密环境文件。它不启动服务、不改公网路由。不要在聊天发送 App secret、令牌或私钥；执行者只需取得配置已完成的脱敏回执。这是实际缺失身份配置，完成后才能沿现有授权继续启动、路由与云端登记，无需重复申请已有部署许可。

签名密钥需要稳定保管，OAuth 持久值由 `oauth_storage.py` 用 Fernet 加密后保存到受限专用目录；加密密钥由稳定签名材料派生。重启时复用同一合法状态，不能每次启动随机换密钥或退回只存内存。配置文件、密钥和 OAuth 状态均不进入 PUBLIC 仓库。

OAuth 配置缺失、主体限制缺失或回调配置无效时，服务必须 fail closed。不要改成无认证模式来取得连通样本。任务数据中的 `sender`、`recipient`、授权描述和目标引用都不是认证身份；用 `whoami` 单独回读服务实际识别的主体。初版按获准单用户使用，不推定多租户隔离能力。

## 4. systemd 与回环服务

使用本仓提供的 systemd 配置模板，明确应用工作目录、独立解释器、受限环境文件、持久状态目录及运行身份。入口命令使用独立解释器运行 `server.py`，由环境文件提供有效配置。进程仅监听回环地址，当前端口为 `8766`；单元应保留错误退出信息，宿主 journald 保留量须有界。

模板设置 `MemoryHigh=192M`、`MemoryMax=256M`、`CPUQuota=50%`、`TasksMax=32`、`LimitNOFILE=256`。它们是保护上限，不表示服务只会使用某个固定资源量，也不证明在这些限制下已满足响应时间、恢复或长期稳定性。

主进程警告日志使用 `RotatingFileHandler`，每份 1 MiB，当前文件加两份备份约 3 MiB；应用不启用 HTTP access log。bootstrap 错误仍送 journald，单元限制日志速率但不能据此推定磁盘总量。配置错误退出码为 78，`RestartPreventExitStatus=78` 避免配置缺失时反复重启。

启动前先验证配置完整和目录权限。启动后分别读取单元状态、实际进程、回环监听及服务错误；`active` 或端口监听只代表进程层，不能代替 MCP 初始化、OAuth 或客户端工具调用。

systemd 的进程重启只恢复信箱服务，不触发任务执行，也不拉起 Mac／Herdr／模型。保留原任务与事件引用，应用重启不创建替代任务。

## 5. HTTPS 前缀与认证路由

新增 `/mailbox/` 路由时保留既有 Gitee 路由，并先验证反向代理配置，再采用目标服务支持的安全 reload。需要同时核实：

- Caddy 模板对新入口请求体设置 `4MB` 上限；此为 HTTP 包络限制，不替代 Store 的单份内容字节检查。
- 外部 `/mailbox/mcp` 到回环 MCP 路径的前缀处理，避免重复或遗漏路径。
- OAuth 元数据、授权、令牌和回调地址采用正确的外部 HTTPS host／scheme／前缀。
- MCP 的认证挑战与授权服务器发现可由真实客户端读取，回调不会落到 Gitee 或其他应用。
- Streamable HTTP 的请求、会话与响应能穿过代理；HTTP 200 不能替代真实 MCP `initialize`、`tools/list` 和工具读写。

带前缀的 OAuth discovery 已在临时回环测试通过；本轮生产 HTTPS 的两类 discovery、受保护 MCP 的 401 挑战和无凭据 token POST 已实测。真实授权后的 MCP 工具调用尚未通过，不应将路由样本宣布为整条链路通过。先验证未认证访问受到保护，再在用户完成授权后验证允许主体；不要在公开命令、截图、日志或报告中粘贴访问令牌。

## 6. 登记云端和 Mac 客户端

只有新 HTTPS 端点及 OAuth 路由已通过实际验证，才登记客户端。云端配置使用 `https://<PUBLIC_HOST>/mailbox/mcp`；本地 Codex／Kimi 也配置相同端点，各自使用原生客户端支持的 OAuth 登录机制。具体配置字段以当前客户端版本的实际 schema 为准，不把一份客户端配置机械复制给另一客户端。

各端模板与操作入口见 [clients/README.md](../clients/README.md)。模板不携带服务端 OAuth secret，不能将未替换的占位插件当成已连接服务。

登录与授权由公子在各自原生界面完成。分别回读云端、Codex 和 Kimi 的工具发现与 `whoami`，不能将本地发现冒称 Dot 发现，或将 OAuth 登录成功冒称全部工具可调用。

最后按 [双向验证步骤](verification.md#双向-nonce-验证)执行一轮数据闭环：Dot 提交，Mac 读取并回传，Dot 读取并核精确版本／摘要；再由 Mac 提交、Dot 回传、Mac 读取。分别记录调用者、原任务、版本与实际读取。只连接到 MCP 不会自动通知、唤醒或派发本地任务。

## 回退与保留

新路由或服务失败时，保全 SQLite、OAuth 状态和脱敏证据，停止受影响的新配置步骤。仅撤回本次新增的服务／路由，不删除既有 Gitee 或业务资源，不迁移 UNKNOWN 任务或更换执行入口。认证明确失败时保留原因，由原身份配置负责人处理。
