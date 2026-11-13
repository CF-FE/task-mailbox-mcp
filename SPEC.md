# VPS task-mailbox MCP：最小实现范围

公子要求创建连接云端 Dot 与本地客户端的 MCP，部署在现有 VPS，并登记云端供两侧调用；源码、文档与占位配置独立维护。既有 Gitee 服务、网络与 Herdr 控制绑定不迁移。

本项目维护与 Issue 目标固定为 `CF-FE/task-mailbox-mcp`；整合目录、机器或聊天位置不是仓库归属证据。其他项目任务仍绑定各自原仓库与正式 Project，不集中到本服务仓。

## 2026-11-14 已采纳的平台扩展

[ADR-001](docs/decisions/001-task-collaboration-platform.md)已由公子采纳并授权实施：[首期合同](docs/implementation/platform-v1-contract.md)约束 PostgreSQL、监控 Web、观测接入、Agent 查询／上报与配套 Skill。下文的 SQLite 存储／页硬配额／无界面是既有实现合同；平台切换时按 ADR 明确升级该范围，其余原九工具语义、原 ID/hash/版本/游标、取消及验收边界保持。PostgreSQL 的 usage v2 不伪装 SQLite 页统计，256 MiB 变为告警阈值；公子已排除数据库备份工作。当前实际部署状态仍查验证记录，不因采纳而假定已切换。

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

## Mac 本机协调员端口 v1

原 `bridge.py run` 在同一 collector 进程和原网络锁内，可选监听一个本机 Unix socket。启用文件固定为桥 `config.json` 同目录的 `coordinator-port.json`。无此配置时仅运行原收发。端口不会加载第二个 OAuth 客户端、发起执行或确认读取。VPS MCP、游标及旧 job 哈希保持原合同。

**授权来源。** 本机密钥授权特定生产者调用本端口。云端原生主体与 Project item 的证明须由宿主维护方在带外按原 `job_id/payload_hash`、原始发送回执和实际 Project item 回读独立核准，再逐原件写入私有配置。服务没有签发云端身份的能力。共享账号、正文、项目别名和任意可打开 socket 的进程均不能新增 grant。宿主应把每份配置限定到已核的桥 source、consumer 和唯一协调员台账。来源凭据不足时保持空 grants。

- 配置结构为 `{v:1,socket_path,keys,grants}`。keys 映射本机 key ID 到 32 字节随机密钥的 64 位小写十六进制。grant 精确包含 `key_id,actions,project_id,coordinator_id,recipient,report_route,job_id,payload_hash,origin,origin_ref,task`。同一 job 只有一份绑定，凭据和实际路由留在宿主。origin 字段为 `channel,subject`，task 为 `repository,project_ref,item_id,task_ref,evidence_ref`。actions 只允许显式勾选下述三个业务操作。以 `probe-` 开头的 key 只可探测，写入此类 key 的业务 grant 导致配置拒绝。
- 每个连接只处理一个操作。UTF-8 NDJSON 每帧含 LF 上限 2 MiB，同时最多 4 个连接，单连接总时限 5 秒。服务先发 `{v:1,challenge}`，challenge 为新生成的 64 位十六进制串。请求为 `{v:1,op,...,auth:{key_id,mac}}`。规范化 JSON 使用排序键、UTF-8、紧凑分隔符、禁止非有限数值。请求 HMAC-SHA256 覆盖 ASCII `MBRIDGE/1`、NUL、challenge、NUL、去掉 auth 的规范化请求。已认证响应 `{v:1,ok,value|code,server_mac}` 的签名覆盖 `MBRIDGE/1/REPLY`、NUL、challenge、NUL、请求 mac、NUL、去掉 server_mac 的规范化响应。所有 MAC 为小写 hex，所有 NUL 均为一个零字节。未知 key、错误 MAC 或非法初始帧可收到无签名拒绝。没有有效响应签名就没有可采信的成功或已证实未入队。
- `probe()` 只返回 `{service:"original-bridge",protocol:1,operations:["verified_job","lookup_report","enqueue_result"]}`。成功只证明本机签名往返。 `verified_job(job_id)` 返回可信 grant 中的 origin、origin_ref、task 及原 job_id、payload_hash、record。记录未保全时回 `UNAVAILABLE`。经认证的读取可要求原 collector 在既有共享时间与 RPC 预算内补读一条已知且摘要一致的原 job，仅在原请求上重查。没有本机路由记录的 ID 不会触发远端补读。原件持久保存在桥既有本机配额内，最多 64 份、小于等于 8 MiB 且不超过数据库上限四分之一。没有自动清理或扩容。
- `lookup_report(job_id,event_id)` 按原事件查本机 outbox。已认证的 null 代表该 job 本机没有该事件。 `enqueue_result(project_id,job_id,payload_hash,event_id,status,result)` 接收逐件授权的协调结果；缺省授权仅限只读 QUERY_BLOCKER，目标交办另见下述类型合同。服务核原项目、原请求、任务与 tracking、结果状态、核心版本／摘要和稳定事件 ID。核心结果上限 256 KiB，最终带 tracking 的 wire 结果上限 1 MiB。事件 ID 为 `coordinate-` 加规范化 `[coordinator_id,project_id,request_id,job_id,core_version]` 的 SHA-256。重复相同事件返回原记录，冲突拒绝。两种操作的记录字段为 `job_id,event_id,status,result_hash,state,receipt`。PENDING 表示已保存本机；SENT 附原服务器版本和哈希回执。云端精确读取与 ACK 仍走原受权读取方。
- 拒绝码为 `ACCESS_DENIED`、`INVALID_REQUEST`、`CONFLICT` 和 `UNAVAILABLE`。未知结果、超时和丢确认只按原 job/event 查，不能由客户端换事件重发。仅读和排队按动作各自授权。

**部署与恢复。** 安装规则及签名探针步骤见 [本机操作说明](mac/README.md#协调员本地签名探针)。未取得现任负责人、当前云端Dot和项目项的独立证明之前，只能验收已安装服务的探针及隔离数据。端口不提供身份登记、填充任命、远程开通、自动写 Projects 或代理业务 ACK 的操作。

### 逐件目标交办的数据合同

桥端代码增加 `DELEGATE_GOAL` 数据校验，不执行任务、联系负责人或自动开通权限。现有 grant 缺少 `request_kind` 时仍只允许 `QUERY_BLOCKER`；目标交办必须显式配置 `request_kind:"DELEGATE_GOAL"`。显式 `QUERY_BLOCKER` 与省略该字段复用同一查询原件证明，已捕获的两种写法均可回读。目标类型保留独立来源摘要。其余类型拒绝，类型字段与 `transport_probe` 不得混用。`probe-` 密钥仍不允许任何业务 grant。新代码或文档存在不代表宿主已经部署或批准具体目标。

目标请求的 `coordinator_request` 精确字段为原 `schema_version,request_id,idempotency_key,kind,expected_generation,deadline,authorization_ref,evidence_refs,budget` 加 `goal`。预算固定为 `{dispatch_attempts:1,production_writes:0,audio_generations:0}`，整数不得用布尔值替代。`goal` 必含 `objective,work_key,acceptance_criteria,evidence_refs,related_request_ids,scope_refs,allowed_actions,next_check_at,review_requirement`，唯一可选字段为 `acceptor_ref`。协调目标的 project、job、request、idempotency、related request 与 action ID 均为 1–128 位 ASCII 字母、数字或 `._:-`，首位须为字母或数字。资产引用保留原值，只接受可打印且无首尾空白的字符串。验收标准、证据、资产范围和允许动作非空且不重复；允许动作仅为 `coordinate_existing_owner,verify_existing_evidence,plan_independent_review,report_progress`。目标复查时刻须带时区且不晚于原期限，受理时的新鲜度由协调核心核验。

发起方可以用 `goal.acceptor_ref` 指定业务验收者。引用限 1–2048 个可打印 Unicode 字符且无首尾空白，保留原值。桥拒绝空值、占位符、云端Dot的已知组织别名，以及与已核原生来源主体精确相同的引用。验收者任职与真实权限由协调核心和所属项目核验。省略时由协调核心按原件绑定的项目负责人代次确定验收者，桥保留原件，既有摘要和结果结构不变。

`work_key` 是规范化 `{task_ref,scope_refs:sorted(scope_refs)}` 的 SHA-256，`task_ref` 使用已核 grant 的正式 Issue URL 或 draft ID；payload 中等价的 `github:仓库#编号` 别名不产生另一份工作身份。原 payload/hash、job、来源主体证明、Project 条目、收件者与回程仍须精确相符。桥保留并验证外部合同，不跨仓导入协调运行模块。

回传继续使用 `PROJECT_COORDINATOR_RESULT` 包装及原 job/event/hash，允许多个独立核心版本。协调员按核心版本顺序入队，原 collector 按本机持久入队顺序补传。同毫秒入队及原事件重试保留该顺序。目标业务报告必须是 `GOAL_UPDATE`，并绑定 `work_key`、原请求、action、generation、来源主体与原生证据引用。报告代次必须等于原请求冻结的 `expected_generation`。核定原生负责人、动作、报告序列及听检证据由受信生产者和协调核心负责，桥只接收逐件授权生产者的签名数据。跨代交接需另行取得有效绑定。业务阶段单列 `goal_state=ACCEPTED/IN_PROGRESS/BLOCKED/COMPLETED`，与 `report.phase` 一致；外层 `REPORTED_COMPLETED` 只表示这一份报告已提交，不代表目标完成。目标进度不能作为 `BLOCKER_REPLY` 或旧查询结果发布。

非终态报告含 `seq,phase,summary,observed_at,evidence_refs,next_owner,next_check_at`；终态改为 `terminal_condition,criteria_evidence`，逐项覆盖原验收标准。要求独立听检时还须有 `independent_review`：审查者与生产者须使用不同的可打印身份引用且无首尾空白，并提供精确资产范围、原件证据，方法只能是 `human_listening` 或 `audio_perception`。ASR、技术计数或空泛通过不能充当该合同；桥只验证被证明来源的记录结构，不自行判定听感。历史报告的复查点可已过去，查回原事件不据此失败。

`COORDINATOR_WAIT_REPORT` 可回传等待快照，但必须标明 `original_kind:"DELEGATE_GOAL"`、`goal_state`（仅 `ADMITTED/ACCEPTED/IN_PROGRESS/BLOCKED`）和 `last_seq`，没有负责人业务报告字段，也不允许声明 `COMPLETED`。它不能新增接受或完成事实。稳定事件重试、冲突拒绝、撤回授权、原 collector 补传及原端读取确认沿原合同保留。本次开发不创建密钥、grant 或 pilot，不修改运行配置。

## 原计划的单件连通性任务

ARCHITECTURE TRIAGE
- Path: BOUNDARY_NOTE
- Evidence: 原 collector 已核原件并持久保存，签名本机端口已有 `verified_job / enqueue_result / lookup_report`，独立协调员负责结果生成，原 outbox 负责上传。
- Boundary impact: grant 可显式增加 `transport_probe:{nonce,expires_at}`，仅允许一件固定 ID/hash 的确定性数据回执；不增加操作、进程执行点、VPS 工具或网络客户端。
- Preserved: 协调桥只核验、保存和补传；Mac 协调员必须实际读取 nonce、独立生成并持久化答复。普通业务准入、来源证明、模型和负责人联系不由此授权。
- Next action: 验证默认关闭、单件准入和稳定事件补传，再从固定提交安装；实际首发、处理、读确认单独记录。

这是连通性适配，不是任意任务自动执行器。宿主只为获准的单件配置专用 key 和 grant；`probe-` key 仍无准入权。额外字段中的 nonce 为 16–128 位 ASCII 字母、数字、下划线或短横线，expires_at 为 UTC `Z` 时间。任务必须使用 v1 原有字段，最多另带原有 tracking；target 精确为 `{service:"transport-probe",operation:"sha256"}`，content 为 nonce，不能附带命令、模型指示或 coordinator_request。job/hash、收件者、回程和任务引用均按 grant 核对。

原 collector 实际取得并核验原件后，协调员才能经签名 `verified_job` 读取它。协调员计算 UTF-8 nonce 的 SHA-256，先保存自己的处理记录和稳定结果，再调用原 `enqueue_result`。结果仅包含 `schema_version:1,type:"TRANSPORT_PROBE_RESULT",job_id,payload_hash,operation:"sha256",nonce_sha256,execution_enabled:false,business_accepted:false`，有 tracking 时沿原机制附带完全相同的 task_tracking。状态为 REPORTED_COMPLETED，仅指确定性探针完成。

稳定报告事件为 `transport-probe-` 加规范化 `[job_id,payload_hash]` 的 SHA-256。同事件同内容回查或补传，不创建新版本；冲突拒绝。取消或到期后拒绝首次新增报告，已持久的原报告仍可查回、补传。移除单件 grant 不删除任务、结果、游标、OAuth 或历史处理记录。origin/origin_ref 在此模式下是宿主预授权的试验说明，不能冒充实际云端调用者或调度运行证明。

沿原云端 Dot 已有检查计划预设这件请求，保存配置时不提交、不立即运行。自然运行先查原 job，仅明确 NOT_FOUND 才提交同一冻结包；超时或未知只按原 ID 对账。提交后按获准等待时间读取精确结果版本，核完整内容/hash 再写固定 ACK。首发和读取均由原 Dot 完成，Mac 不代 ACK。配置步骤与无人干预的运行分别留证，不把网页预投单、共享账号或计划刻点当作自然首发证据。
