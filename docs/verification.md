# 当前验证记录

本页是本仓当前验证状态的唯一入口。状态按 `PASS`、`PARTIAL`、`FAIL`、`UNKNOWN`、`NOT_RUN` 记录，始终限定证据所属层次与调用者。保存、发送、持久接收、结果报告、实际读取及业务验收分别取证。

## 当前状态

最近更新于 2026-11-04（Asia/Shanghai）。本服务代码、文档、配置、Skill、插件和 Issue 的唯一维护仓固定为 `CF-FE/task-mailbox-mcp`，初始基线 `7d162860c10ac2d72719522be7fe01478f4747a3` 保留。其他项目依其原 CF-FE 仓库／Project 任务合同维护；本地目录及部署整合来源不能据以改投个人仓，也不把其他业务统一归入本服务仓。

本轮从个人整合仓固定来源 `9d36feb436264f2c0a065be7e644043a930a98dd` 接回 47 个服务文件，排除个人整合记录 `INTEGRATION.md`／`import-manifest.json`；复制时来源 hash 一致，逐文件基线见 [source-updates.json](source-updates.json)。本页随后进行归属与权限更正，来源记录保留复制时字节。本次接收已提交为 `652406bf09ed589285942293129215d35363a19e`，组织规则增量为 `3add47a`，均已推送本仓；个人整合仓只保留历史部署副本。

Mac 桥已于 **2026-11-04 10:48（北京时间）** 升级到 [PR #4 合并提交 `560b9f5`](https://github.com/CF-FE/task-mailbox-mcp/commit/560b9f5de160caf989cfe48d6bc5a83fb0a7bd1a)。13 个运行文件全部匹配该提交。原 launchd 服务已加载新进程，签名端口探针通过，10:48:31 原 collector 完成一次远端轮询（`OK / ROUND_COMPLETE`）。10:49:11 又出现升级前已有的 `NETWORK_FAILED`（`CLIENT_OPEN / RuntimeError`），原因仍为 `UNKNOWN`。10:49:47 本机端口仍就绪。切换前后 8 件任务、24 条收件事件、8 份报告和 5 份已保存原件一致，业务授权及待补传报告均为 0。此次改动位于 Mac 桥，VPS 服务代码无变更。

历史安装 `b6dc6b6dafff08f4ba25ddd999df4e679bb4a802` 的本机安装、隔离联测及生产探针状态见[11 月 3 日现场回执](#2026-11-03-mac-协调员端口现场回执)。前次安装固定为 `87cfb6551240c08d5aa8df3a8afdc305c5388a86`，11 个运行文件摘要回读一致；登录自启、原生 launchd running 和新的 collector=OK 成功时点已回读。观察范围仍为 Herdr／信箱两个项目，信箱路径已切到本 CF-FE 工作区，原数据验证临时绑定已归档解除，原 job／hash／事件／结果与专用 OAuth 身份保留，迁移中的路径元数据适配见下文。VPS 运行代码快照仍为原部署 `a06dc70e7994df7cb3c92a30cfde4276c26a5759`，本轮未重启或替换 VPS 服务。旧 Mac `6386d7f` 及来源 `9d36feb` 作为历史快照保留，不冒称当前来源；除特别标明，现场证据由主执行侧直接取得。

| 层次 | 状态 | 已有证据与范围 |
| --- | --- | --- |
| 标准库 Store | PASS | 17 项临时 SQLite 测试；持久行为、事件去重、配额、满库回滚及旧数据保全 |
| 回环 MCP，小内容／两个模拟客户端 | PASS | VPS 临时回环实测 9 工具、收发、重复投递、摘要确认、账号拒绝及带前缀 OAuth 元数据；不代表 Dot／Mac 原生客户端 |
| 当前约 240 KiB UTF-8 样本 | UNKNOWN | 扩大上限后的重跑观察／SSH 超时，没有取得本次完整结束回执；不补造 PASS，也不派发模型 |
| OAuth 加密存储原型 | PASS | VPS 独立环境、临时目录验证加密持久化、稳定密钥重开及拒写预算；不是生产 OAuth |
| VPS 独立环境与源码 | PASS | 24 个初始来源摘要核对；独立 venv 125212 KiB，锁定依赖与专用单元已安装 |
| VPS 常驻新信箱进程 | PASS | 单元 active/running，NRestarts=0，回环受保护端点返回 401；只证明进程层 |
| 共享 HTTPS 路由与发现 | PASS | 两类 OAuth discovery 为 200 且 issuer/resource 精确匹配，MCP 未认证 POST 为 401；Caddy 校验成功、原主进程保持，旧 Gitee 路径仍为受保护 401 |
| 生产 OAuth 跨客户端覆盖 | PARTIAL | 云端 App 与 Mac 桥均取得允许主体的实际工具回执；本地 Codex 原分支交换失败、Kimi 未登录，分别保留状态 |
| 旧 portable 插件 | PARTIAL | 初次包元数据上传通过，但界面显示“仅限桌面端”；公子已卸载，不再作为当前入口；未由主执行侧验证后台永久删除 |
| 新 Task Mailbox App 连接 | PASS | 公子通过原生自定义 MCP 流程创建；主执行侧回读 USER／PRIVATE、1.0.0 及原生 App 绑定，未重造服务器或复制凭据 |
| 当前桌面会话经云端 App 调用 | PASS | 9 工具已实际出现，原生 whoami／mailbox_usage 调用均成功，执行开关为 false；调用者为当前桌面会话，不冒称指定 Dot 已读 |
| 指定 Dot 只读回读（用户回报） | PASS | 公子明确转述指定云端 Dot 的 whoami／mailbox_usage 均成功；这是用户回报的 Dot 实测，不是主执行侧直接以 Dot 身份调用，也不代表原 nonce 已读取 |
| Mac Codex 配置 | PASS | 原生命令登记，回读 enabled=true、Streamable HTTP 及正确端点；不推定当前 App 会话已加载 |
| Mac Codex OAuth | FAIL | CLI 0.166.0-alpha.5.1 本地 callback 后 token exchange 报 Request failed；原因仍未定位，未自动重登或改变认证方式 |
| Mac Kimi 配置 | PASS | 委托执行侧原子写入 Kimi Code 2.4.1 用户级 MCP 文件、保留既有条目；主执行侧另行直接回读端点匹配与权限 0600 |
| Mac Kimi OAuth／工具发现 | NOT_RUN | 未启动 Kimi 新会话、登录或发现 |
| 原 nonce 结果交付与 Dot 读取 | PASS | Mac 桥收件／原事件上传 v1，当前桌面会话经云端 App 读全核摘要／nonce 并 ACK；公子另报告指定 Dot 已读取并确认，主执行侧实际回读该精确 job／版本／hash 的独立稳定 ACK 事件。Dot 行为归用户回报，事件持久记录为主执行侧直接取证；Mac 随后实际收取该 ACK，原游标推进且待处理／回传均为 0 |
| 双向 nonce 全覆盖 | PARTIAL | 上述原任务链路已通过；原任务由桌面会话经云端 App 提交，不能冒称 Dot 发起。Mac 新建任务、Dot 回报的反方向尚未执行 |
| Mac 本地实现与本仓复测 | PASS | 本仓完整 135 项测试已由主执行侧在既有独立 Mac 环境复跑通过：原 Store 17、台账 20、collector 40、hook 10、CLI／安装 8、浏览器交接／日志 4、引用校验 21、关联综合 9、HELD 查询 6 |
| Mac 桥独立安装 | PASS | 独立源码副本、摘要回读、0700 运行目录、0600 配置／钩子定义、实际 SQLite 状态回读及 plist lint；没有启动模型／Herdr／业务 handler |
| Mac 桥依赖与加密客户端初始化 | PASS | Python 3.13.1 x86_64，FastMCP 4.0.11、MCP 2.3.0、cryptography 48.0.1，共 66 依赖；临时目录中实际构造客户端、私密权限与缺缓存拒绝测试通过，无外部请求 |
| Codex App 原生钩子触发 | PASS | 公子已信任定义，早期已直接观察SessionStart／PreToolUse／PostToolUse／UserPromptSubmit；本次Lead真实自然Stop又实际将暂存结果入outbox并由collector上送。只覆盖已有证据，不推定七类全覆盖或CLI每类事件均触发 |
| Mac 桥专用 OAuth | PASS | 公子再次明确要求重新授权后，同一专用客户端完成 token POST 200、后续 MCP 请求 200 及 whoami 成功；保留自有加密缓存，未改代理／TLS／端点。此前尝试的交换状态仍为历史 UNKNOWN |
| Mac 桥真实网络收件／回传 | PASS | collector 实际接收原 JOB_PERSISTED，get_job 核原 ID／payload 摘要；小结果先本地安全保存并入 outbox，再以原 event_id 上传，回执精确匹配 v1／hash。当前桌面会话经云端 App 实际读全并核对原结果后 ACK；无模型／业务派发 |
| Mac 人工收件窗口 | PASS | 手动运行 120 秒，启动 3 轮；结束时 collector=OK、待处理事件／报告均为 0，进程已自然退出。只证明此短窗口，不提升为长期在线 |
| Mac 持续收件进程 | PASS | 公子明确授权推进后，用户 launchd 已 bootstrap；RunAtLoad=true、KeepAlive=false，原生回读 running／PID 及新的 collector=OK 成功时点。网络错误由进程内退避，认证／协议错误停止；没有异常退出自动重启或长期稳定性验收 |
| CF-FE 原 GitHub 连接写入 | PASS | 同一原连接实际创建并独立回读 CF-FE/task-mailbox-mcp Issue #1，关联原正式总览后回读 Repository=CF-FE/task-mailbox-mcp、Status=In Progress；未改 OAuth 权限或换凭据。个人整合仓403不推广为CF-FE授权故障 |
| 信箱自动消费／无人值守业务派发 | NOT_RUN | 信箱／桥没有执行能力；独立[Lead显式Mac适配器](https://github.com/CF-FE/herdr-4-codexcloud/blob/main/docs/herdr-executor.md)已取得首版限定执行／回传工程验收，生产业务仍未验 |

VPS 当前运行时为 Python 3.14.4、FastMCP 4.0.11、MCP 2.3.0、uvicorn 0.54.0。真实端点、账号、插件 ID、原始输出及认证状态留在私密落点，公开文档只用别名。

本机 CLI 回读 `hooks stable true`，未自定义 `CODEX_HOME`，有效用户 hook 文件为 `~/.codex/hooks.json`。同一 Codex host 的 App／CLI 使用这套配置；原共享配置验证阶段的真实事件来自App，当时未运行CLI模型turn；后续独立Herdr适配器已完成一个CLI合成回合，但模型执行与各类CLI hook触发仍须分开取证，不能由共享配置推定。台账保存的是最近观察与时点，不证明原生进程持续在线或业务完成。

## 现场资源与排障边界

部署前单次快照：总 RAM 950.5 MiB、available 658.1 MiB，根盘空闲 13031.3 MiB，1 分钟负载约 0.13。既有 Gitee venv 120.3 MiB，本服务独立 venv 122.3 MiB。早期模拟测试进程峰值 RSS 121444 KiB（约 118.6 MiB），不代表当前大样本或生产负载。

本轮已回读的常驻单元快照 `MemoryPeak=125485056` 字节（约 119.7 MiB），同次 `MemoryCurrent=34807808` 字节（约 33.2 MiB），NRestarts=0。这是一个时点／启动以来峰值，不是持续余量或 24 小时稳定性验收。

Mac 登录使用客户端原生 CIMD、PKCE S256 和 read:user；临时 loopback 接收端经回读确认属于该次 Codex 登录进程。回调已返回，随后令牌交换失败。仅凭 Request failed 不能归因为回调、GitHub 配置或服务端 OAuth 拒绝。应用错误日志为空也不能证明该请求未到达（应用未启用 access log）。

不带真实 code／token 的 Mac token POST 探针返回标准 JSON 401；HTTP/1.1 与 HTTP/2 两种 curl 请求均完成。它们只证明相应探针经过入口，不代表 Codex 的 Rust HTTP 栈、实际授权码交换或云端连接。当前未读取客户端令牌缓存、未重放授权码、未开启可能记录 code／token 的认证 DEBUG 日志。

委托只读诊断在同一 Mac 使用 Codex 0.166.0-alpha.5.1 的 exec-server stdio 发送一次无凭据 dummy POST，2.767 秒返回 HTTP 发送错误，未取得状态码；OAuth／模型调用和配置变化均为 0，主执行侧回读了受限诊断产物。它复现了独立于 OAuth 业务的原生发送失败；该探针与 MCP 登录的 TLS fallback 标记不同，不能当作完整等价流程。具体 TLS／代理／路由原因仍 UNKNOWN，未据此切换配置。

Mac 桥的第二次受控登录只记录异常类型链，不记录可能含凭据的异常 str／args。另用 httpx2 做无凭据诊断：一次 issuer GET 在 2.233 秒、响应头到达前出现 RemoteProtocolError，同次 MCP POST 得到 401；随后以同一默认参数和同一发现 URL 再做一次 GET，5.043 秒得到 200，issuer 与 token endpoint 前缀正确。这些结果支持偶发传输故障的判断，未构成 OAuth 拒绝证据，也没有确定具体 TLS／代理／路由根因。没有改变代理、TLS 或端点，不能将诊断样本当作登录成功。

针对上述已复现的响应头前中断，新增仅同源两类 well-known 元数据 GET 的一次窄重试，固定 httpx2 2.16.0；两次请求共用 10 秒，不替换底层代理／TLS、不跟随重定向，也不在 HTTP 层重发任何 POST。主执行侧运行 71 项标准库回归及 9 项专用依赖假传输测试均通过，后者含实际 OAuth／MCP 共用工厂的离线接合；这些本地测试不提升授权状态；后续真实登录结果按下一段单独记录。

此前安装窄重试后的受控登录已越过元数据发现，但在 SDK 额外的授权页预检 GET 中再次出现响应头前断开，尚未打开新浏览器。已改用 SDK 支持的原生浏览器回调：只向同源 HTTPS 精确 `/authorize` 地址交接原 URL，不重复 headless 预检，保留 PKCE／state／服务端校验；worker 仍拒绝新交互。实际接合测试确认零额外 HTTP、一次原 URL 浏览器交接。为避免 SDK 异常日志包含认证响应，只记录客户端日志级别／异常类型；随后一次已打开浏览器但在传输中断后仍无 token／发现缓存；公子再次要求重新授权，同一客户端正常完成 token POST 200、后续 MCP 200 与 whoami。最后这次成功证明当前通路恢复，不反推旧授权码是否消费或旧 token 是否签发，也不构成长期稳定性证据。

## 本地 Store 验证

在仓库根执行：

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时数据库与标准库，不导入 FastMCP、不调用网络、Herdr 或模型。测试数据不会成为业务任务。17 项通过仅证明已覆盖的本地行为，不能推导 VPS 磁盘恢复、OAuth 生命周期或长期在线能力。配额测试使用至多 64 KiB 预算的临时满库样本，证明新增结果失败时任务／结果／事件事务回滚且旧记录保留，并非现场故障注入。

MCP 集成验证单独在服务的独立依赖环境执行：

```bash
.venv/bin/python tests/smoke_mcp.py
.venv/bin/python tests/smoke_oauth_storage.py
```

`smoke_mcp.py` 无参数，使用临时回环端口和 StaticTokenVerifier 测试身份；通过时只证明此测试范围内的 MCP 协议与工具收发，不证明生产 OAuth、VPS 部署或 Dot 调用。不得将集成测试加入标准库测试的隐式导入路径。报告须分别保留 Store 测试、回环 MCP 测试和生产端点实际调用结果。

OAuth 存储脚本使用临时目录验证 Fernet 加密持久化、稳定密钥重开及保守拒写预算。初测由委托侧使用既有依赖完成；主执行侧随后在 VPS 新独立环境直接执行同一脚本，验证通过。两者均不构成生产授权通过。

## 资源保护的验证边界

默认配额为单份任务／结果 1 MiB、SQLite 主数据库 256 MiB、最多 10000 个 job，可配置。`mailbox_usage` 只读报告计数、数据库页使用量及上限；它不报告整个服务目录、主机磁盘或 OAuth／日志总量。任务条数满额拒绝新任务，主数据库满额拒绝无法持久化的写入，不自动删除、清理或扩大上限。

OAuth 专用存储默认采用 8 MiB／512 文件的保守拒写预算，要求单进程唯一写者，不是宿主硬配额。主进程日志为 1 MiB × 3 文件、约 3 MiB；bootstrap 错误仍进 journald。Caddy 模板限制请求体 `4MB`；本轮已安装并回读相应保护配置；尚未执行生产容量／满额故障测试。

systemd 模板的 `MemoryHigh=192M`、`MemoryMax=256M`、`CPUQuota=50%`、`TasksMax=32`、`LimitNOFILE=256` 均为保护上限，尚非实测资源保证。配置错误退出 78 不自动重启，应用不启用 HTTP access log。rollback journal、OAuth 状态、journald 和依赖不包含在主数据库 256 MiB 内；这些项目的独立容量与长期观察仍未完成。

## 端点与认证验证

依次验证：回环服务及真实 MCP 协议、外部 HTTPS 前缀、认证挑战／发现、独立 OAuth 回调、允许主体、各客户端工具发现与 `whoami`。每层保留服务版本、调用者、时间、脱敏请求引用及结果。

明确区分 systemd `active`、HTTP 返回、MCP 初始化、工具清单和工具调用；其中一项通过不能填补其他项。用户完成 OAuth 登录前，受保护工具调用保留 `NOT_RUN`；明确认证拒绝记对应分支 `FAIL`，不静默降为无认证。

独立 GitHub OAuth App 的私密配置和本地 hook trust 已由公子完成，不再作为缺失项。旧 portable 包已由公子卸载；新原生 App 的工具清单、whoami／usage 已由当前会话直接取得，指定 Dot 的两项只读成功另按用户回报记录。Mac 桥使用专用加密 OAuth 状态，不读取旧 Codex／Kimi 缓存或绕过认证；本桥已取得完整授权回执并沿原 nonce 完成 Mac 收件／补传及当前会话经 App 的读取确认，未重建替代任务；指定 Dot 原结果读取已有用户回报及直接事件回读，Mac 发起反方向另验。

云端只读工具成功与云端 hook 能力分开。[官方企业边界](https://learn.chatgpt.com/docs/enterprise/agent-security#hooks-in-local-computer-access-with-work-cloud)规定：相关 enterprise managed policy 和 remote hooks 启用时，Work Cloud with local access／Dot 可使用 Global `requirements.toml` 中的管理员远程 `mcp_tool` hooks；personal accounts 和无 local access 的 Work Cloud 不适用这些企业 hooks。本地 command／插件 hook 不支持云编排；当前 Dot 的企业 hook 条件未验，不承诺自动唤醒。

## 双向 nonce 验证

本节只验证任务和结果数据的传输，不向 Herdr、shell 或模型派发测试任务。

1. Dot 生成全新 `job_id` 与随机 nonce，按约定规范化 JSON 计算 SHA-256 后提交；保留服务返回的持久任务记录。
2. 指定 Mac 客户端主动读取事件并调用 `get_job`，核对同一 ID、payload 和摘要，记录真实读取时间。
3. Mac 使用全新稳定事件 ID 上传包含 nonce 关联和接收证据的小结果；同一事件重传只查原版本，不新增结果版本。
4. Dot 实际读取该结果，核对 ID、版本、结果摘要，再按精确版本／摘要调用 `ack_result_read`。
5. 反向使用另一个新任务：Mac 提交，Dot 主动读取并回传，Mac 实际读取并确认。两方向分别记录，不以一方调用代理另一方。

不得把 `sender` 字段、自报读取时间或中间转述当作指定客户端实际读取证据；认证主体与原生客户端回执分别保存。若当前会话只能在本地模拟两侧，结果只记录本地协议测试，Dot 端仍为 `NOT_RUN`。

发生超时或结果不明时，沿原 job／event／cursor 查询，不重建替代任务。取消工具只登记请求，不停止任何执行；读确认不代表业务采用、正式验收或写权交还。

## 后续证据写法

每次更新注明日期／时区、执行侧、版本、测试范围、原任务与事件引用、精确结果版本／摘要，以及尚未覆盖项。公开文档使用稳定别名，实际地址、账号、令牌、配置和原始输出留受限落点。

短时数据闭环不能推导 24 小时可用性。故障注入、主机重启、真实 Herdr／模型回合、业务任务或控制入口切换按各自有效授权另验，不由本页测试结果自动开启。

## 2026-11-02 Mac 安装与当前续测引用

实现运行目录、配置和加密状态均留本机受限位置；只观察公子选定的 Herdr／信箱项目。`SessionStart`、`UserPromptSubmit`、必要工具前后、`Stop`、`Interrupt`、`SessionEnd` 只保存关联与最近观察。结果须显式绑定 session/turn/job，Stop 成功入 outbox 后归档原件；失败保原件，不从回复或 transcript 自动生成业务结果。

依赖安装曾遇旧内网额外索引超时、PyPI 下载超时及 Linux cryptography 清单不适用于本机 Intel 二进制的问题。最终保留主框架固定版本，按 Mac 平台二进制解析并单独锁定；未更改全局源、既有客户端或系统 Rust。66 包清单见 [Mac 锁定文件](../mac/requirements.lock.macos-py313.txt)。

原 nonce 为当前桌面会话经新 App 提交的“仅数据验证”任务。Mac 桥授权成功后，collector 从原日志持久接收并实际 get_job；仅从远端取回的内容计算 nonce 摘要与反序结果，先保存本地结果和原事件 outbox，再上传 v1。主执行侧经同一云端 App 实际读取该版本，与 Mac 原件逐字段及规范化 SHA-256 核对一致，nonce 变换一致，随后登记精确版本／摘要的读 ACK。私密续测记录保留原 job、payload hash、稳定事件、结果及回执；未重建／重发任务。该链路是桌面会话经云端 App 与 Mac 桥的真实闭环；指定 Dot 自身读取已有公子明确回报，主执行侧从服务实际回读独立稳定读 ACK，原 job／v1／hash 一致；这区分用户报告的 Dot 行为与主执行侧直接取得的持久事件证据。Mac 发起反向任务仍未执行。没有 Herdr／Agent 或模型执行。

上线／清理状态：旧包卸载及指定 Dot 两项只读成功归公子回报；新 App 原生元数据／当前会话调用、四类 App hook 台账回读及 Mac 桥 OAuth／原 nonce 收件回传归主执行侧。Mac 模拟子进程／fake transport 仍只作本地测试；其余原生事件、CLI 模型触发、长期稳定性、反方向及业务验收逐项未决。没有执行现场断网／崩溃注入、业务清理或任意自动派发。

## 2026-11-02 后续 Projects 协作关联

公子重申既有约定：项目代码、文档、Skill、插件及配置模板在所属 GitHub 仓库维护，所有后续项目任务／迭代沿正式 Projects 及原任务关联。排期明确沿用各项目已有 Iteration，不新建统一日期或周期。实际总览及私有引用只保存在受限回执；公开契约与模板见 [docs/projects.md](projects.md)。

当前已建立一个私有统一总览，主执行侧直接回读可见性、字段和两个视图。已登记 Herdr、Task Mailbox、凡人二创三个项目导航及本轮关联任务；导航不填写业务 Status。已有业务 Project 的四项在途任务状态由原板保管，未为汇总覆盖状态或迁移执行；其余项目还须逐一登记，不声称所有项目已接入。

此前 Issue create 的 403 Resource not accessible by integration 发生在个人整合仓 `ghyghoo8/create-network-sh`。该目标 public、Issues 已启用、用户 ADMIN，不证明 connector 对该仓库有 Issue 写权限；这次拒绝不属于 CF-FE 仓库的授权故障，也不能推广为所有连接写权限不足。Projects MCP 缺少 draft 创建能力时，主执行侧已用本机 gh 在原获准私有总览建立任务 draft，并经 Projects MCP 回读原 item／draft／Status；该历史草稿及回执继续保留。

公子明确仓库归属后，主执行侧通过原 GitHub connector 回读安装范围，确认包含 `CF-FE/task-mailbox-mcp`；同一 create_issue 操作成功创建 [CF-FE/task-mailbox-mcp#1](https://github.com/CF-FE/task-mailbox-mcp/issues/1)，独立 fetch 回读成功，随后加入原获准私有总览并独立回读 In Progress。未修改授权、更换凭据或变更 OAuth；CF-FE 的该 Issue 写路径为 PASS，个人整合仓的差异独立保留。正式任务采用该 Issue，旧 draft 转为历史来源并保留；历史 job／hash 及在途执行不变，不重派。私有安装、Project、item 及 job 标识仍只留受限回执。

原个人整合仓的代码／安装 `6386d7fa7ba1780adf10bd20af0e669f2f389a5a` 与验证基线 `9d36feb436264f2c0a065be7e644043a930a98dd` 仅作为历史来源和运行快照。新增实现已按上述固定来源接回本仓；本仓新提交、安装来源切换和部署验收各自记录，不由文件复制推定完成。

本轮实现只改 Mac：可选 payload.tracking 保存在本地 routing、binding、会话摘要和只读 hook 提示；业务绑定／报告缺关联时拒绝，结果按原 job 自动附 task_tracking 并拒绝冲突。旧数据任务仍可收件查看和直接数据报告。引用标记 caller supplied／UNVERIFIED，不能当作 GitHub 实际核读；VPS 九工具、数据库与执行权限未变。

### 真实条目关联样本：PASS（数据层）

该数据样本当时由主执行侧经 Projects MCP 实际核读总览中的原任务 item、原生 draft ID 与 In Progress，再把这组引用随一个新的独立纯数据 job 提交。Mac 常驻进程实际接收，原 job／payload hash 与本地 tracking 一致；当时真实协调会话已显式绑定，引用仍标 UNVERIFIED，不冒称桥自行访问 GitHub。后续补充的 CF-FE Issue 不改写这个样本的原 draft 关联。

本地从已持久收件摘要生成小结果，queue_report 自动附同一 task_tracking 并保存原事件。没有启动第二个网络收件器；原 launchd 进程自动补传取得 v1。当前桌面会话通过原生云端 App 实际读全，与本机安全保存的原件逐字段、规范化 SHA-256、tracking 摘要比对均一致，随后发送精确读 ACK；原后台又实际收取 ACK，持久 cursor 前移，待处理事件和报告均为 0，collector 最新为 OK。完整 ID／hash／回执保存在受控落点与原私有任务正文。

期间曾观察 NETWORK_FAILED，原进程按既有退避继续，随后恢复成功并上传同一事件；未换代理／端点／凭据、未重建任务、未现场注入故障。该自然错误样本与恢复不覆盖所有断网／崩溃情况或 24 小时可靠性。此样本由当前桌面会话经云端 App 发起／读回，不冒称指定 Dot 执行，也没有 Herdr／模型／业务派发。

### CF-FE 本仓复测

主执行侧在本 CF-FE 工作区使用既有独立 Mac 解释器执行 `python -m unittest discover -s tests -q`，104 项全部通过。服务与 Mac 代码来自上述固定闭包；本地复测不等于此新提交已安装，实际源码提交与运行回执在后续部署记录补充。

### 固定组织与 Mac 维护路径落地

公子进一步明确固定的是 GitHub 组织 `CF-FE`：各项目仍使用独立仓库，Task Mailbox 使用本仓。Mac 已从本仓固定提交 `652406bf09ed589285942293129215d35363a19e` 安装，11 个运行文件摘要与本仓一致；原生 launchd 回读 running，新的 collector=OK／成功时点，持久 cursor 与待发记录保留。

本地观察范围继续只有 Herdr 与信箱项目；后者路径已从个人整合副本切到本 CF-FE 工作区。端点、消费者身份、数据库和专用 OAuth 缓存未变。原数据验证会话绑定已存入私密迁移回执并解除，旧 job／payload／hash／结果和事件未改写，避免继续把历史个人仓测试引用提示成当前正式任务。CF-FE Issue 创建及读回来自原 GitHub 连接，没有修改应用权限或换凭据；原个人仓403独立保留。

### 自然认证停机与受控恢复

在上述常驻运行之后，本地 2026-11-02 23:51:34 观察到 collector=AUTH_FAILED／CLIENT_UNAVAILABLE、进程退出 1，上一成功时点为 23:51:02；待处理事件与报告均为 0。仅凭该健康码不能断言具体 401、refresh、用户拒绝或令牌失效。

主执行侧仅检查本桥自己的加密缓存元数据：token／refresh token／client／discovery 均存在，本机 expiry 仍有约 6 小时；这些元数据不证明服务端必然接受。随后使用原缓存做一次有界只读 whoami，实际三个 MCP POST 均为 200、工具成功，没有 token 请求或交互登录。基于这条新的验权证据，主执行侧显式 kickstart 原单元一次；历史失败根因仍为 UNKNOWN，不能把一次恢复提升为长期稳定性。未改 TLS、代理、端点或凭据，未重发任务。

## 本地组件命名（2026-11-03，Asia/Shanghai）

公子指出本地安装名与云端插件容易混淆，本地组件统一改称 **Mac 协调桥（Mac Coordination Bridge）**；VPS 信箱及 MCP 插件仍称 **Task Mailbox**。源码、文档、CLI／授权引导和 hook 台账提示已同步，104 项本地回归通过。固定实现提交为 `4a11219462bf101569605e5dfe676f39ba38e219`，已复制到本机并核对全部 11 个运行文件摘要；真实 CLI `--help` 已读回新名称。

既有安装目录、launchd 标识、钩子定义和专用 OAuth 保留，配置／钩子／plist 的改名前后摘要一致；钩子标签还用于定义去重，未为改名重新生成定义或要求信任。后台 collector 未因纯命名更新重启；当前进程加载的历史名称和既有 OAuth 注册元数据可继续显示旧称，新客户端名用于未来注册。此项验证只证明命名与安装文件，不扩展长期稳定性或业务验收结论。

## 安装路径迁移（2026-11-03，Asia/Shanghai）

状态：**PASS，目录与后台切换完成，新钩子已由公子确认信任且真实触发。** 本次只迁移本地桥，不改 VPS、云端 App、信箱端点、source_id、consumer_id 或 Herdr／信箱两个项目的感知范围。

- 当前运行目录为 `~/Library/Application Support/MacCoordinationBridge`；默认配置、七类 hook 命令、启动参数、工作目录及日志路径均已适配。旧 `TaskMailbox` 目录不存在，不保留兼容软链接；旧启动项已卸载并移到私密回退材料中，没有第二个收件器。
- 运行源码固定为本仓 `9d556e052e4efd29ad3dafbaf72834b1707c1591`，11 个运行文件摘要与该提交一致；106 项标准库回归通过。新的独立 Python 3.13.1 环境按同一锁定文件重建，66 个依赖版本与旧环境一致，`pip check` 和关键模块导入通过。本机原包镜像超时后，公子明确批准仅本次使用官方 PyPI；仅子进程覆盖下载配置，全局 pip 配置未改。
- 切换前暂停本桥 hooks、停止旧 collector 并等满 3 秒钩子超时窗口，再核对无数据库持有者。整体移动原状态目录；SQLite 完整性、切换前后数据库文件摘要及所有表快照一致，draft 保留。启用后原 jobs／inbox／reports 仍与切换前快照一致，游标为 7，待收事件及待补传报告为 0。随后新增的 hooks／sessions 及 health 属真实运行记录，未重写历史正文、payload hash、结果或读 ACK。
- OAuth 的密钥及 4 个加密值文件在切换前后字节一致；4 份 FileTree 集合元数据只将 `directory` 从旧缓存路径映射到新缓存，其余字段不变。新目录中禁止交互登录的客户端离线初始化通过；后台继续使用原专用缓存，无重新注册或交互登录。启用后的直接快照还确认 9 个 OAuth 文件与启用前摘要一致；这不是长期不刷新令牌的承诺。
- 新启动项为 `io.maccoordinationbridge.coordinator.plist`，`RunAtLoad=true`、`KeepAlive=false`。主执行侧直接回读 launchd running、启动后的新 collector=OK／成功时点和原游标；启动时间为本地 07:58，后续成功收件在该时点之后。原认证失败停分支及网络退避规则不变，短时成功不代替长期稳定性。
- hook 标签为 `Mac Coordination Bridge local coordination`。公子已确认信任，主执行侧以迁移前私密 SQLite 快照为界，直接回读当前会话新增 PreToolUse／PostToolUse；仅记录本次实际已见的事件，未把配置或手动脚本调用当作原生触发。其他原生事件与 CLI 模型 turn 仍分别留待取证。

配置／旧环境／旧源码／旧 plist 及迁移对账摘要保存在新运行目录下的私密 `state/evidence/runtime-path-migration-20261103/`。它们是回退材料，不是另一个活动安装；原台账与 OAuth 只沿新路径继续。公开仓库只保留方法、固定提交和汇总结论，不提交私密配置、凭据、完整路径清单或原始现场输出。

## 全局评审实施与固定版本交接（2026-11-03，Asia/Shanghai）

本节为原任务的统一工程交接；代码／作者自检、独立增量复核、实际运行回读与最终验收分别记录。公子在原 review 会话授权按“归属和状态对齐 → 三缺陷修复 → 运行更新 → 独立验收”推进，本实施会话是本轮相关资产的唯一写入协调者，原 review 会话负责拉取和最终验收。原 Issue／Projects 关联不变，不新建竞争队列。

### 固定版本与维护边界

下表保留原工程验收时的发布快照；公子后续授权的三仓发布及部署差异复核见本节末尾，不以旧快照覆盖后续实际远端。

| 仓库 | 本轮固定版本 | 维护／发布事实 |
| --- | --- | --- |
| `CF-FE/task-mailbox-mcp` | HELD 增量 `8fd78b8d1b76a917d9ce91fb0d4257f45b0560d9`；完整修复源码 `87cfb6551240c08d5aa8df3a8afdc305c5388a86` | 已推送本仓；VPS 信箱与 Mac 桥唯一维护位置。本页文档提交由 Git 历史和原 Issue 记录，不与运行源码版本混同 |
| `CF-FE/herdr-4-codexcloud` | 本地 `c2d88bdb12d3ff9638e00c433f7b0f457d5c7593` | 9 份采用／状态文档对齐；Worker 已在原本地 `f9b1209` 提交。本次独立读取远端 main 为 `2d812e96486579e71443d32f1c46d81a0b5fd749`，本轮未推送，不能将 Worker 或新文档记为已发布 |
| `CF-FE/mymcp-4-codexcloud` | 本地 `f864c5f17d11f543ead1ef2d9cd366ea87c8828a` | 首页新路线和角色接收导航两文档更新；旧 Lody 任务和写权未宣称全部退出。本轮未推送，已核远端 main 仍为 `02e6ca4` |
| 个人 `create-network-sh` | 本地 `a45cdc404cabfe2e4f580c681aef2efe928f8e10` | 只改 4 份历史标记／导航；服务源码与 import-manifest 未变、不自动同步。本轮未推送，已核远端 main 仍为 `9d36feb` |

Herdr 的 `README`／`docs/status.md`／`architecture.md`／`pilot.md` 与 5 个相关现行入口已对齐；有日期的早期接入保留原文并显式标历史。Worker 的远端目标文件缺失已实际按远端提交树核对，mymcp 不再引入该未发布文件的 main 直链。各仓只维护自己的权威状态，本节不替代原业务 Project 或 Herdr 采用状态。

### 精确变更文件

以下为追加实施包 T1–T3 的文件集合；各路径均相对所属仓库根，不包含运行缓存或私密证据。

| 仓库 | 文件 |
| --- | --- |
| Herdr | `README.md`、`docs/status.md`、`docs/architecture.md`、`docs/pilot.md`、`mcp/README.md`、`docs/agent-communication.md`、`docs/collaboration-iteration-guide.md`、`docs/dot-mac-herdr-evaluation.md`、`docs/vps-control-pilot.md` |
| mymcp | `README.md`、`docs/lody4dot/workspace-roles/README.md` |
| 旧整合仓 | `README.md`、`fastmcp/README.md`、`fastmcp/task-mailbox/README.md`、`fastmcp/task-mailbox/INTEGRATION.md` |
| Task Mailbox | `mac/collector.py`、`mac/bridge_store.py`、`mac/bridge.py`、`tests/test_collector_recovery.py`、`tests/test_collector_recovery_additional.py`、`tests/test_held_query.py`、`SPEC.md`、`README.md`、`mac/README.md`、`docs/verification.md` |

已完成的 T0 迁移另以 `9d556e0` 保留 `mac/config.py`、`mac/install.py`、`tests/test_mac_runtime.py` 及安装说明的路径／新旧 hook 去重适配；其后验证文档基线为 `ca8a480`。T2 不重复迁移、不重建环境或改 hook 定义。

### 三缺陷、行为回归与边界

1. **收件／补传饥饿**：两向共用唯一总 RPC 计数及 deadline，worker 每轮轮换优先方向；RPC 至少 3 时为后方向保留 1 次调用，1–2 RPC 不强分。曾发现固定半轮时间会让合法的两 RPC 结果在连续出件下永久 pending，已用真实 `run_collector` 加 fake client／有界 stop 复现并修正；最终版本不再固定减半时间。已知传输故障可在剩余预算给另一方向机会；未知／本地错误结束当前轮，认证／配置／整体协议失败仍停止分支。报告沿原 event/hash/result；不会重跑业务。关键回归：`test_pending_query_timeout_does_not_starve_saved_report`、`test_upload_timeout_does_not_starve_new_page_and_job`、`test_pending_backlog_leaves_an_upload_opportunity_inside_total_budget`、`test_real_worker_does_not_starve_slow_two_rpc_result_with_continuous_outbox`、`test_two_rpc_worker_eventually_advances_both_directions`。
2. **安全原因码丢失**：现有 health.detail 记录白名单 `CAUSE stage:STAGE type:TYPE`，时间沿 updated_at；未知明示 UNKNOWN，认证原因优先于外层协议包装，关闭客户端的后续网络错误不能将认证停止降为自动重试。原始异常、URL、token、payload 和未许可类型文字不写入状态；单事件 HELD 原因也只保留已定义安全码。关键回归：`test_safe_auth_cause_and_creation_stage_survive_wrapping`、`test_known_outer_protocol_code_keeps_inner_authentication_stop_cause`、`test_later_context_close_error_does_not_erase_authentication_stop`、`test_untrusted_auth_text_and_exception_type_are_not_persisted`。**2026-11-02 的 AUTH_FAILED/CLIENT_UNAVAILABLE 现场根因仍 UNKNOWN**，测试构造的注册过期不能反推现场。
3. **HELD 无定位入口**：`read_held_events(...)` 及 `held --cursor N --limit N` 使用只读 URI，核 source／consumer，返回原 cursor/event/job、类型、原因及安全时间。查询不构造新 Store、不创建缺失台账、不改变任何表、收件游标、HELD 配额或队列，不依赖 OAuth 或网络。关键回归：`test_pages_cover_held_only_in_cursor_order_without_bodies_or_writes`、`test_rejected_boundaries_leave_every_table_unchanged`、`test_reopened_ledger_and_readonly_cli_match_without_oauth_or_state_changes`。精确范围及并发分页边界见 [SPEC](../SPEC.md#mac-本地-held-诊断接口)。

上述新回归位于 `tests/test_collector_recovery.py`（16）、`tests/test_collector_recovery_additional.py`（7）及 `tests/test_held_query.py`（6）；原 collector 的 17 项停止／预算／去重回归保留。没有增加数据库 schema、MCP 工具、依赖版本或认证入口。

### 统一检查与独立复核

| 检查 | 结果与证据归属 |
| --- | --- |
| Mac 独立解释器 `-B -m unittest discover -s tests -v` | 主执行侧直接 135 PASS，绑定完整修复源码；含真实临时 SQLite 与实际临时 CLI，非模型／业务调用 |
| 同环境 `-B tests/smoke_client_transport.py` | 主执行侧直接 9 PASS，全部 fake／临时资源、无外网；另对已装 SDK 的 ReadTimeout／RemoteProtocolError／EndOfStream 做无网络识别及安全详情断言通过 |
| Herdr 三项规定检查 | 主执行侧直接通过 `worker_library.py check`（7 Worker／9 backend／13 role／11 专业候选）、`check_repository.py`（mcp0/skill2/plugin0）、27 项 unittest。后续只有 README／status 采用文字补录，代码树未变 |
| mymcp | 主执行侧结构检查通过（mcp1/skill4/plugin1）；仅两 Markdown 改动，未重跑无关全套行为。239 PASS／1 Linux skip 限复用原 review 会话在未变代码基线 `02e6ca4` 的结果，不冒充本次重跑 |
| 文档、来源与增量 | 子执行侧完成三仓 15 Markdown 的引用核对；验收侧独立报告 268 个本地链接／72 锚点通过。主执行侧核源码／清单未跨仓变更，legacy import-manifest 字节保留，最终 diff --check 通过 |
| 独立模拟与代码增量 | 原 review 主线程直接执行：初始独立 9 项由 5PASS/4FAIL 转为 9PASS，新增生产 worker 路径 2PASS；HELD 9PASS 由其子审查实际执行，主线程绑定文件摘要回读该增量。collector SHA256 `f62f01502d1eeaebeac8e3ee6444381802ef95e525d0637ef8c9ae87a8ee1a85` 以及 HELD 已验文件摘要均与固定修复提交一致，增量代码审查通过；不等同整体业务验收 |

验收侧在 135 项中观察到 2 条既有测试夹具 SQLite ResourceWarning，已定位 `tests/test_bridge_store.py` 的 with-connection 夹具未显式 close；该文件本轮未改。无测试失败，作为已知非阻断警告保留，本轮不扩展修复，也不因警告重复运行全部测试。

### 新目录运行回读与交验状态

源码自检完成后，主执行侧停止原唯一 collector，按固定 `87cfb655` 的 Git 对象更新既有 `MacCoordinationBridge/app`，没有再次迁移或新建环境。11 个运行文件同时匹配安装 receipt 和固定源摘要；配置、hooks.json、plist 摘要不变，不要求重复信任。原 meta/jobs/inbox/reports 与 9 个 OAuth 文件在更新前后及启动后仍一致；历史事件、结果、ACK、密钥和授权保留，hooks／sessions／health 的正常新记录不冻结。

主执行侧直接回读：新 launchd running、collector.lock 只有新进程持有、collector 与 collector_last_success 为 OK，且成功时点晚于此次启动；cursor=7，HELD／待补传为0。安装后的只读 HELD 命令实际返回空尾页，未改变全局游标；更新后的原生 hook 已出现新观察。验收侧另直接报告已核同一 receipt 固定源、11 个文件、只读台账、原生 launchctl 与新成功时点，非仅转述作者结论。

完整机器级凭据与摘要保存在私密 `install-receipt.json` 及 `state/evidence/bridge-reliability-update-20261103/`；其中原文件、配置和旧源码仅作回退材料。公开仓库不包含私密端点／会话ID、令牌、台账正文或原始现场日志。VPS 本轮没有部署或重验，原部署快照仍沿记录 `a06dc70`；Mac 改版不冒称 VPS 换版。

**原 review 会话已回读统一交接内容，并明确给出本轮工程范围最终验收：通过。** 回执绑定修复源码 `87cfb655`、已回读交接报告 `d882e6f828a3569656077e39f66463f7ef170b11` 及当前 Mac 运行副本；当前提交只合入该回执和最终文档版本。范围限三仓本地维护归属／状态对齐、三缺陷修复及当前 Mac 更新，不包含三仓尚未推送的发布。135 标准库、9 SDK smoke、Herdr 27 为验收主线程直接复跑；独立 20 项中 HELD 9 由子审查实际执行，其余 11 由验收主线程执行，代码及安装／原生进程／只读台账由主线程另核。作者自检没有替代这些验收。原 Issue 保持 Open。长期稳定性、Mac 发起反向新任务、具体 Cloud／CLI 的独立认证与读写、Herdr／模型业务承接及负责人业务验收仍分别未验；自动执行 handler 与 Dot 自动唤醒未实现。本轮未新增生产 job、模型／Herdr 派发、现场故障注入、自动登录／重注册、TLS／代理／端点变更或新的 CI／调度平台。

### 后续授权的发布与部署差异复核

2026-11-03，公子在原 review 会话追加“都推送，然后部署”。原 review 会话负责三次 fast-forward 推送，本实施主线程又分别直接 `git ls-remote origin refs/heads/main` 核对：

| 仓库 | 已发布 main | 本次部署处理 |
| --- | --- | --- |
| `CF-FE/herdr-4-codexcloud` | `c2d88bdb12d3ff9638e00c433f7b0f457d5c7593` | 已含 Worker／角色库及采用文档；Mac checkout 已完整持有离线闭包，无额外服务安装 |
| `CF-FE/mymcp-4-codexcloud` | `f864c5f17d11f543ead1ef2d9cd366ea87c8828a` | 两份导航文档；不替换在途 Lody 配置 |
| 历史个人整合仓 | `a45cdc404cabfe2e4f580c681aef2efe928f8e10` | 只发布历史标记；不从此部署信箱或同步源码 |

Mac 主执行侧直接回读安装源仍为 `87cfb655`，11 文件同时匹配 receipt／Git；`87cfb655..797afbcb` 的运行文件及部署目录无差异。新 launchd running、仅一个 collector 持锁；本次观察到 2026-11-03T01:29:12Z 的 collector OK 和随后真实 PreToolUse／PostToolUse。故本轮无需再次安装、重启、授权或重建环境；旧目录／旧启动项仍不存在。

VPS 由本实施会话的子执行者直接经原 SSH 入口只读核对：原 systemd 单元 active/running，实际进程和工作目录仍指向原独立应用。主执行侧复核其脱敏 inventory 与本地固定对象；没有冒称本主线程再次 SSH。磁盘三源码及原生单元的摘要与当前维护版本、Mac 固定源、初始基线和历史部署整合树均一致：

| 文件 | SHA-256 |
| --- | --- |
| `server.py` | `cc4a825fc732aa8adfddcea85f8297b08de06733d1ef0dc3a3b7ab8a8d66c2b4` |
| `mailbox_store.py` | `2a18b9a57b12df0def29bcb121f2cc619b57b930eabd9d52818bf483ab4e86ed` |
| `oauth_storage.py` | `868de43dd455ab610685c2c620af8086ce3f54df73327a6b721add3a648e79f5` |
| `deployment/task-mailbox.service` | `34bc93282bd83c5fa10658f99f0b46309c9d1645dfe53b1c974fee75360ea733` |

本次**无需 VPS 部署变更或重启**。原 receipt 的 `source_commit=7d162860` 与 `commit=a06dc70` 分别解析为本仓初始基线和历史整合树，保留原字段，不把“文件相同”改写为当前 HEAD 已安装。脱敏 inventory 记录于 2026-11-03T01:34:41Z；首次成功只读未采远端精确时刻，之后补采请求超时并已停止，精确远端观察时刻为 UNKNOWN。该证据只覆盖磁盘文件和原生进程路径，不覆盖 Python 内存字节、新 OAuth／MCP／端到端或长期验收。未改环境文件、路由、依赖或 Gitee 服务。

Worker／角色库共 22 文件、111,320 字节，仅标准库离线选型／准备，主要由 Dot／Mac Lead 消费；准备稿已携带所选角色及合同正文／摘要。Win／Cloud 无须默认安装全库，具体任务按获准入口取得选中固定输入并核读取即可。若以后要求独立整库消费，再明确接收目录及采用回执；本轮未批量分发、启动 Worker 或创建 Cloud 任务。Mac 官方 Herdr skill 与库是不同对象，既有副本摘要相符，无需重装。

公子另已授权 Herdr 项目协调层确定性执行适配器开发，正式任务归 [Herdr #1](https://github.com/CF-FE/herdr-4-codexcloud/issues/1)，规范及后续执行事实由该仓维护；本 Issue 继续承载传输组件与原关联，VPS 九工具、Mac 桥及 hook 不因此取得执行权。本节的发布／差异复核不是新增适配器、模型回合或业务验收的完成回执。

Herdr 后续限定试点已由其实施主线程完成一次真实执行／产物核验，详见 [Herdr 执行适配器规范](https://github.com/CF-FE/herdr-4-codexcloud/blob/main/docs/herdr-executor.md)；本服务没有新增执行工具或更换运行源码。该结果已用 Lead 自己的真实 Codex session／turn `bind` 和 `stage-report`；真实自然 Stop 于北京时间2026-11-03 10:25:49将其排入原outbox。原collector在既定退避中自行恢复，10:31:52直接回读collector=OK及原report=SENT／v1／同hash；期间没有重启、重新登录、改路由／认证／代理／TLS、第二客户端或业务二发。原review实际读全v1，与2973字节原件逐字段及canonical hash一致，10:34:55写稳定读ACK并再读持久事件；实施主线程直接核本地ACK游标10／HANDLED、待收和待补传0。没有伪造Stop、直接发布或第二collector。主执行侧另直接读到最后成功 2026-11-03T02:12:30Z、02:16:30Z collector=`NETWORK_FAILED / UNKNOWN_CAUSE stage:CLIENT_OPEN type:RuntimeError`；当前原因 UNKNOWN，沿原退避，不据此认定认证失效、重登录或业务失败。原事件已按稳定event／hash完成对账；本Issue因长期稳定性和正式业务范围仍保持Open。此次自然CLIENT_OPEN异常期间的持久缓存和补传已取得一个真实样本，不能代替物理断网／崩溃注入或24小时稳定性。Herdr自身首版工程任务沿其原Issue限定验收，不据此关闭本服务的稳定性待办。

## 2026-11-03 Mac 协调员端口现场回执

现场证据由本轮部署维护方回读并交付。下表将正式 Mac 的安装验收与隔离环境的协议验收分别记录。

| 对象 | 状态 | 回执 |
| --- | --- | --- |
| 正式 Mac 原桥升级 | PASS | 既有正式安装目录已升级到 `b6dc6b6dafff08f4ba25ddd999df4e679bb4a802`。原 collector 在维护窗口内停止、启动，北京时间 18:02:23 回读健康 `OK`。13/13 个运行文件的摘要同时匹配固定 Git 对象及安装收据。 |
| 升级连续性 | PASS | 维护方前后核对原核心台账及 hooks、config、plist 一致。本次沿用原 OAuth 和原状态目录，无迁移。 |
| 隔离工程联测 | PASS | 固定 Herdr `418b5a2` 与本桥 `b6dc6b6`，使用合成身份及临时数据验证本机 socket 双向签名、原 collector 收发、丢确认后按原事件对账，以及指定结果版本／摘要的读取和 ACK。Herdr 97 项、信箱 147 项回归和 9 项 SDK 冒烟检查通过。 |
| 正式 Mac 签名探针 | PASS | 北京时间 18:08:42.833，正式安装的协调员 `418b5a2` 对原桥 `b6dc6b6` 完成签名往返，回读 `PORT_VERIFIED / LOCAL_BRIDGE_PORT / protocol=1`。监听为 `READY`，原 collector 进程为 `RUNNING`，配置及 socket 权限均为 0600。业务 grants 为 0，一次手动 pump 返回 `NO_PILOT`。[探针与轮换步骤](../mac/README.md#协调员本地签名探针) |
| 原 collector 远端轮询 | PASS（单次回合） | 启用探针前，北京时间 18:06:30 回读 `NETWORK_FAILED / CLIENT_OPEN`，具体原因仍为 `UNKNOWN`。重载后，18:10:04.801 回读 `OK / ROUND_COMPLETE`。原桥已有报告 3 份，待补传 0，按生产授权捕获的原件为 0。 |
| 云端 Dot 真实联络 | NOT_RUN | 本次生产协调台账的项目、请求、联系和结果计数均为 0。本轮真实业务请求与负责人答复均为 0，尚无云端 Dot 对本次请求的原生读取回执。 |

## 2026-11-03 原计划回收与单件自然首发适配

当前总任务沿 [本仓 Issue #1](https://github.com/CF-FE/task-mailbox-mcp/issues/1) 和原 CF-FE 项目协作总览维护，保持 Open / In Progress。下表是后续证据，不改写上述 18 时观察的历史范围。

| 对象 | 状态 | 证据与边界 |
| --- | --- | --- |
| 原收件器恢复 | PASS（有限恢复） | 原单元20:51因 AUTH_REJECTED / CLIENT_CLOSE 停止；21:53以原缓存、原锁执行一次禁交互whoami成功，随后只恢复原单元一次。21:55及21:59有新的完整收件OK。未新登录/注册或改端点，现场认证及间歇网络错误根因仍UNKNOWN。 |
| 无结果提醒的回收 | PASS（限定样本） | 原Dot浏览器首发一次；负责人答复经原Mac outbox上传，服务端22:09:32保存v1/cursor18；22:23:42保存精确同版本/hash的ACK/cursor19，Mac22:32处理为HANDLED。原主协调未在发布后提示结果或手工触发计划，随后只读原会话核真实答复和同件ACK。此样本首次投递依赖网页，不能算全程后台首发。 |
| 原计划的独立运行归属 | PARTIAL | 原会话把读取关联到既有休息计划，名义22:30、实际22:23左右；没有独立run_id/调用卡，不认定准点。公子后续要求不再验调度准点性，同一新探针首发后等待5分钟再读，避免反复增加真实任务。 |
| 单件端口适配 | PASS（源码与Mac安装） | 固定源码[2c790d9](https://github.com/CF-FE/task-mailbox-mcp/commit/2c790d9dc0c431aa823a6980cf6bb299abc2505c)已发布。新6项及原端口12项通过，主执行侧一次全仓标准库检查153项通过。23:00:43原Mac完成最小升级，13份文件匹配固定对象及receipt；原业务表、OAuth、config、hooks及plist保全。只保存真实nonce原件、核验协调员回执并入原outbox，桥不生成协调员答案。 |
| 单件临时准入及撤回 | PASS（限定一件） | 新增配对key/grant的部署协调请求先被自动审批以额外访问权限变更拒绝；公子随后明确“允许这一次，完成即撤”，最迟次日00:30到期。只在批准后配置唯一固定job/hash的三个本机操作。23:35:01已撤新增key/grant、删除协调端client；回读原端口配置逐字节等于开通前基线，grants=0，仅原探针key。原collector未重启，结果和历史保留。 |
| 原收件器升级后运行 | PASS（有限观察） | 新进程原端口READY，本单client完成真实签名probe；23:02:31取得新的完整collector OK。期间仍有CLIENT_OPEN网络失败，按原退避，不新授权或反复重启，不升格长期稳定性。 |
| 同件自然首发/协调员自动处理 | PASS（只读连通性） | 唯一包在开通前核实未入箱。原主协调只保存既有休息计划的单件条款，关闭页面后不发消息或点击运行。服务23:28:20持久接收唯一任务/cursor20；Mac协调员经签名端口读取真实nonce，独立持久生成答复，23:28:44进入原桥outbox；服务23:29:17保存唯一v1/cursor21，Mac报告SENT。无模型、任务shell、负责人联系或生产资产写入。 |
| 原Dot实际读取及确认 | PASS（同版本/摘要） | 原Dot自行读回并在23:34:04保存固定ACK/cursor22，v1及结果hash与冻结包、协调员输出、服务及Mac记录一致。原主协调只读原Dot会话确认回报；本实施侧仅查事件元数据及本地台账，没有代理submit、publish_result、get_result或ack_result_read。23:35回读三个事件均HANDLED，只有一个结果事件和版本。 |

本轮结论：限定一件的只读后台投递、协调员自动答复、结果回传和原Dot读确认闭环通过，临时准入已撤。单件合同和到期/补传边界见 [SPEC](../SPEC.md#原计划的单件连通性任务)，协调员源码及独立处理记录归 [Herdr仓](https://github.com/CF-FE/herdr-4-codexcloud/blob/main/docs/project-coordinator.md)维护。任务包与结果在运行时通过信箱自动交接；公子在首发前另转发过沿原计划及5分钟回读的说明，Dot当时查到NOT_FOUND并表示等原计划，该说明不是实际任务投递或结果转交。原计划设置及事后只读验收用到原生页面，与运行触发分记。按公子要求不再验调度准点性，未取得的独立调度器run_id不补造。

VPS、MCP Events均未更新，原台账及OAuth保留。[PR #3](https://github.com/CF-FE/task-mailbox-mcp/pull/3) 已合并到 `main`，合并时间为北京时间 23:41:52，合并提交为 [`eded441`](https://github.com/CF-FE/task-mailbox-mcp/commit/eded44119fd19e5fb70342e504c4b3cd7cf66e00)。原主协调另核Mac的13份运行文件与实测提交及合并树一致，仍为实测时原进程，无需重新部署或新增测试单。这次只读样本不代表24小时稳定性、通用项目自动派发或业务验收；本Issue仍因长期稳定性待办保持开放。
