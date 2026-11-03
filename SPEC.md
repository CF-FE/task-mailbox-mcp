# VPS task-mailbox MCP：最小实现范围

公子要求创建连接云端 Dot 与本地客户端的 MCP，部署在现有 VPS，并登记云端供两侧调用；源码、文档与占位配置独立维护。既有 Gitee 服务、网络与 Herdr 控制绑定不迁移。

本项目维护与 Issue 目标固定为 `CF-FE/task-mailbox-mcp`；整合目录、机器或聊天位置不是仓库归属证据。其他项目任务仍绑定各自原仓库与正式 Project，不集中到本服务仓。

## 用户流程与边界

1. Dot 或本地客户端持久提交带固定 `job_id` 的任务数据。
2. 对方主动按游标读取、查询原任务，再按已有授权执行；提交不直接执行任何命令。
3. 执行方上传小结果，对方读取精确版本与摘要并确认。

同一 VPS Streamable HTTP MCP 端点提供工具，无附加界面、AI 或消息平台。Mac Herdr 仍直接管理既有 Win 目标；信箱不访问 Herdr、shell、任意本地路径或模型。配置 MCP 不自动唤醒 Mac，不建立后台轮询，也不保证无人值守。

## 数据与接口

- 请求规范化：UTF-8 JSON，`sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False`；`payload_hash` 使用 SHA-256。
- payload 必含 `schema_version=1`、`origin_task_id`、`sender`、`recipient`、`report_route`、非空 `target` 对象及 `content` 文本。目标、身份别名和授权描述都是调用方提供的数据，不能冒充原生执行／认证证据。
- `submit_job`：同 ID 同内容返回原记录；冲突拒绝，不覆盖。
- `get_job`、`list_events(cursor, limit, recipient)`：持久查询与有界增量读取。
- `publish_result`、`get_result`：稳定事件 ID 去重；不可变结果版本及 SHA-256。
- `request_cancel`：仅登记取消请求；不宣称 Agent 已停止。
- `ack_result_read`：精确匹配结果版本与摘要；只是调用方读确认，不是业务验收或写权交还。
- `whoami`：独立返回服务认证主体；sender/recipient 不能用于绕过认证。
- `mailbox_usage`：只读返回任务／结果／事件计数、数据库页数／页大小／分配字节及配置上限，不包含业务内容或私密数据库路径。

默认单份任务／结果最大 1 MiB、SQLite 主数据库最大 256 MiB、最多保存 10000 个 job，分别由 `TASK_MAILBOX_MAX_PAYLOAD_BYTES`、`TASK_MAILBOX_MAX_DATABASE_BYTES`、`TASK_MAILBOX_MAX_JOBS` 配置。数据库预算至少 64 KiB，每连接按真实页大小设置 `max_page_count`。统计中的 `allocated_bytes` 为页数乘页大小；数据库页预算不包括 rollback journal、OAuth 状态、日志或依赖。

任务数统计包含已报告结果的 job；达到上限后拒绝新 ID，原 ID 的查询与相同内容重传继续可用。数据库达到页数上限时，新增写入明确失败并回滚事务；不承诺满库后还能新增结果或读确认。配置小于已有数据库占用时明确拒绝，不删除旧记录。服务没有自动删除、过期、清理、vacuum 或扩额任务。

小结果直接存 VPS 本地 SQLite，任务变更和事件同一事务提交；不设计跨机共享数据库。使用 rollback journal 和同步 FULL，避免初版引入 WAL 检查点维护。数据库满额／磁盘故障不触发外部业务副作用。

## 运行与认证

服务源码、依赖与运行目录独立于 Gitee。复用 VPS 的 Python／FastMCP 版本信息，不修改既有虚拟环境。公网连接必须有 OAuth 与单用户授权限制；缺少新服务的有效 OAuth 配置时拒绝对外启动，不降为无认证。云端与本地可使用同一 OAuth 入口；凭据仅留宿主私密配置，不进仓库、插件包或报告。

`oauth_storage.py` 对持久 OAuth 值使用 Fernet 加密，派生密钥绑定稳定签名材料，重启后复用。专用后端目录默认保守拒写预算为 8 MiB／512 文件，分别通过 `TASK_MAILBOX_MAX_OAUTH_BYTES`、`TASK_MAILBOX_MAX_OAUTH_FILES` 配置。预算要求单进程唯一写者；它不是目录或宿主硬配额，不通过驱逐现有记录腾空间。

公子需先创建独立 GitHub OAuth App，并在宿主 TTY 通过 `deployment/configure-private.py` 以 getpass 输入新 secret。脚本保留本服务已有签名密钥，只写权限 `0600` 的私密环境文件，不启动服务；不要求在聊天传凭据。这是初次部署的身份配置前置；本轮已完成配置，实际授权状态见验证记录。

现有 Gitee 域名／8765 端口保持；新服务的回环端口、HTTPS 路由与 OAuth callback 必须沿宿主实际配置核定后记录。新增路由前校验，保留原配置；不启动第二个冲突隧道或更换既有 URL。

systemd 模板使用 `MemoryHigh=192M`、`MemoryMax=256M`、`CPUQuota=50%`、`TasksMax=32`、`LimitNOFILE=256`。这些是保护上限，不是实测占用、吞吐或长期稳定性保证。Caddy 入口模板限制请求体为 `4MB`，独立于规范化任务／结果字节限制。主进程警告日志按 1 MiB × 3 文件轮转、约 3 MiB，应用不启用 HTTP access log；bootstrap 错误仍进 journald，配置错误退出 78 且 systemd 不自动重启。OAuth 目录和 journald 另行观察容量，主数据库配额不能限制整个服务目录。

## 验证与交付

1. 标准库 Store 行为测试：持久化、重复／冲突、并发、游标、结果版本／摘要、取消与读确认、资源配额、满库回滚与只读统计；当前 17 项通过。
2. 在独立依赖环境通过真实 MCP initialize、tools/list 与受保护访问验证。
3. VPS 服务、HTTPS、OAuth、本地客户端、云端登记和双方 nonce 双向读写逐层记录。
4. 只有 Dot 与指定本地客户端各自实际读取对应 nonce，才记双向链路通过；本机会话调用不能冒称 Dot 调用。

VPS 临时回环重跑已取得 9 工具、两个模拟客户端收发、账号拒绝和 OAuth discovery 的 `PASS`。OAuth 加密存储已由主执行侧在 VPS 临时目录、新独立依赖环境直接测试通过。这些均不代替生产 OAuth 或真实两端调用。当前状态以 [验证记录](docs/verification.md)为准。

服务范围不包含模型回合、业务任务、故障注入、正式控制权迁移、主机重启或 24 小时稳定性验证。

## 后续事件与 Codex App 钩子

[事件与钩子方案](docs/hooks.md)已有 [Mac 实现](mac/README.md)：单 collector、原子本机收件／游标、项目关联、会话观察、显式 session/turn/job 报告和稳定 ID 补传。公子已授权推进这一范围，感知限定 Herdr 与信箱项目；生命周期钩子仅作适配器，不执行任意任务。VPS 九项工具契约、取消语义和认证边界保持不变，云端 Dot 唤醒、信箱自动消费／无人值守派发仍未实现。独立的[Lead显式Mac执行适配器](https://github.com/CF-FE/herdr-4-codexcloud/blob/main/docs/herdr-executor.md)在Herdr仓完成首版限定工程验收；它不属于本服务／桥的执行能力，不扩大到Win／Cloud、任意任务或生产业务。

## GitHub Projects 关联增量

ARCHITECTURE TRIAGE
- Path: BOUNDARY_NOTE
- Evidence: 现有 payload v1 完整持久化额外字段；Mac routing 原来只保留目标和报告路由，项目协作规范已有 Projects／Issues 权威入口。
- Boundary impact: 新增可选 payload.tracking 的本机持久关联、项目会话绑定准入及 result.task_tracking；现有数据任务收发兼容，VPS 九工具与业务执行／写权不变。
- Preserved: Projects／原任务持正式状态与验收，仓库存项目资产；Mac 只收件和补传，不新增 GitHub 凭据、自动写 API 或 AI。
- Verification: 引用结构、不可变身份、绑定／暂存／Stop／outbox 连贯性与旧数据路径测试，以及真实条目引用的纯数据收件／回报。

详细字段、Issue／Project draft 两类来源、沿用各项目 Iteration 和派发前核读见[关联契约](docs/projects.md)。引用中的 UNVERIFIED 状态不代替 GitHub 读回；读确认不自动完成或关闭任务。

## Mac 本地 HELD 诊断接口

ARCHITECTURE TRIAGE
- Path: BOUNDARY_NOTE
- Evidence: `mac/bridge_store.py` 已拥有 inbox 的 HELD 状态与原因；当前 CLI 仅有计数。既有 `BridgeStore` 构造会初始化数据库，因此诊断入口必须绕过构造写入。
- Boundary impact: 本地模块新增 `read_held_events(path, source_id, consumer_id="mac-coordinator", *, cursor=0, limit=25)` 和 CLI `held --cursor N --limit N`，使用 SQLite `mode=ro`；不新增 MCP 工具或数据库字段。
- Preserved: 台账所有权、身份、全局收件游标、HELD 配额占用、原 event/job、结果和状态不变；无网络、OAuth、释放、重派或删除操作。
- Verification: 交错状态分页／重复查询、空页、边界拒绝、全表及文件摘要不变、无正文泄漏、实际临时 CLI 无 OAuth 收件环境运行。

`cursor` 为本次诊断的排他下界，整数范围 0 到 2^63-1；`limit` 为 1 到 100 的整数，默认 25，两者均拒绝布尔值。结果为 `{events, next_cursor, has_more}`；按原 inbox cursor 升序，仅列 HELD，每页不超过 limit，空页保留输入 cursor。返回字段仅为 cursor、event_id、job_id、event_type、bridge_state、reason 和 created_at；后者只返回含时区的可解析 ISO 时间，否则为 null，不回显无约束时间字符串。原因沿原安全原因码；任务／结果正文、data、路由和凭据均不返回。

查询核对数据库中的 source_id／consumer_id；数据库缺失或身份不符即拒绝，不创建目录或初始化新台账。`next_cursor` 只供下一次诊断分页，绝不写回收件 cursor。并发状态变化下分页不是快照：较早 PENDING 后转为 HELD 时，应从 0 重新检查；不能将继续分页当作完整历史通知或自动承接证据。
