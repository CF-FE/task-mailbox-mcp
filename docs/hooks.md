# Task Mailbox 事件与 Codex App 钩子方案

日期：2026-11-02，Asia/Shanghai。状态：**本地实现与隔离测试已完成，已独立安装并由公子审阅信任；真实 App 已观察四类原生 hook，Mac 桥授权及原 nonce 收件回传已通过，常驻收件已按公子指示启用，长期稳定性未验。** 本页是事件与钩子扩展的唯一当前方案；它不授予自动执行权限。当前现场与客户端状态统一见[验证记录](verification.md)。

后续项目业务绑定与报告另须遵循[Projects 关联契约](projects.md)：原仓库存资产，原 Projects／任务正文持正式状态与排期；本地只保固定引用与回执。

## 结论与保留的主张

建议采用 **Mac 主动拉取既有 events → 本机 SQLite 持久收件与游标 → 本地通知或获准的受控 handler**。VPS 继续只保存任务、事件和结果，不增加 webhook 控制面。

Codex App“编码 → 钩子”可以适配本地 Codex 会话的入场提示和回报收集，作为 executor adapter；它不替代持续收件器，不承诺唤醒空闲 Dot 或开启新模型回合。先做纯通知、原任务关联和持久回报；自动执行另绑定操作、目标、权限和预算。

这保留了建议的核心价值：事件不依赖一次聊天请求返回，Mac 重启后可以继续对账，hook 失败不抹掉已成功持久提交的任务。最强的现实张力是：**当前事件过滤不是面向每个接收者的投递队列，原生 hook 也不是可靠消息调度器。** 未补清这两个语义，就不能承诺“不漏收、自动唤醒、业务恰好执行一次”。

## 已核证据与尚未确立的范围

VPS 运行代码依据目标仓 `a06dc70e7994df7cb3c92a30cfde4276c26a5759`；设计评审前的文档基线为 `c9766cb5f9029ba305706043bfbaf2449c8815fd`。Mac 当前安装固定版本与摘要见验证记录。代码契约位于 SPEC、MCP 函数签名和 Store 校验器；没有独立 contracts/schema 文件；Mac 客户端实现位于 [mac/](../mac/README.md)，服务器契约未变。

| 证据 | 已确立含义 |
| --- | --- |
| `server.py:71-95` | submit 只存数据；recipient 是过滤条件，不是 ACL |
| `mailbox_store.py:191-201` | 事件有全局持久递增 cursor、唯一 event_id，并和任务修改同事务提交 |
| `mailbox_store.py:261-283` | 查询严格 `cursor > saved`，按原任务 `jobs.recipient` 过滤；返回最后已列出的 cursor，无结果时保留输入 cursor |
| `mailbox_store.py:304-335` | 结果是不可变版本；稳定 event_id 重传返回原版本；报告完成不等于业务验收 |
| `mailbox_store.py:343-375` | cancel 只设请求位；读确认精确绑定版本／摘要，不授予停止或交还写权 |
| `server.py:21-30` | 认证门是允许的 GitHub 账号；payload sender／recipient／授权文字仍是调用方数据 |
| `server.py:193-194` | 当前采用 stateless HTTP MCP；没有服务端推送订阅／唤醒接口 |

评审已核 [OpenAI 官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks)及 background hooks 小节；本机回读 `hooks stable true`，未自定义 `CODEX_HOME`，有效定义位于 `~/.codex/hooks.json`。公子已审阅信任七类定义；主执行侧直接回读 live 台账，确认当前 session 对应本对话，已观察 SessionStart／PreToolUse／PostToolUse／UserPromptSubmit。计数持续增长，本页不固定数字；其余三类及 CLI 模型 turn 的原生触发未据此通过。

服务启动、HTTPS、客户端配置与原生 hook 支持分别取证，均不自动证明该客户端的认证、工具发现或业务执行。

## 采用、删除与暂缓

| 处置 | 内容 | 理由 |
| --- | --- | --- |
| ADOPT | 复用现有全局 events，Mac 出站连接，本机持久 inbox／cursor／dispatch 关联 | 增量查询和事件 ID 已存在，无需增加 VPS 回调入口 |
| ADOPT | 每个消费目的有稳定 consumer_id；单个 Mac 收件器串行入库；游标和收件页同事务 | 防止请求成功但本机尚未落盘时跳过事件 |
| ADOPT | Codex 本地 hook 只作生命周期适配；默认 advisory，未绑定时仅感知，不生成业务回报 | 利用原生事件，不再造第二个执行管理框架 |
| ADOPT | 首轮只通知及保存回报；handler 默认关闭 | 收到数据不等于取得执行许可 |
| DELETE | “recipient=Dot 可收到 Dot→Mac 任务的结果”假设 | 当前 SQL 按原任务收件者过滤，结果不会改投 Dot |
| DELETE | 不同 recipient 或消费者共用一个高水位 | 过滤视图和消费进度不同，会静默跳过另一侧旧事件 |
| DELETE | 在 SQLite 事务中等待 HTTP、模型或 Herdr | 会长时间持有写锁；网络成功也不能和 SQLite 原子提交 |
| DELETE | `Stop` 等于业务完成；直接把 last_assistant_message 发给信箱 | 会话停顿不是业务回执；末条文本未必绑定任务，也可能混入其他内容 |
| DELETE | 用 Stop blocking 或无限 async hook 维持无人值守轮询 | 前者会自动续模型回合，后者不唤醒空闲会话且可能随会话结束被取消 |
| DEFER | VPS webhook、消息中间件、另一层 AI 协调 | 当前没有可抵消新增故障面的必要证据 |
| DEFER | 多机器自动抢占／故障转移、自动重跑 UNKNOWN、硬停止保证 | 当前无 claim／fencing／原生停止回执契约 |
| DEFER | 服务端 stream_id／重建检测、可恢复多消费者租约 | 第一阶段限定同一未替换持久数据库；正式迁移恢复需补对应契约 |
| DEFER | 云端 Dot 自动唤醒／自动开新 turn | 当前账号、产品入口和实际能力没有取得证据 |

## Codex App 原生钩子适配

官方文档区分 `command` 与 `mcp_tool`。后者只调用已有 MCP 连接，不负责启动或重连；同步执行，连接缺失或工具错误不保证阻止原动作，SessionStart 还可能早于 MCP ready，SessionEnd 不支持这种 handler。因此不把直接远端 mcp_tool hook 当成持久收件或结果补传的唯一入口。[官方说明](https://learn.chatgpt.com/docs/hooks#mcp-tool-hooks)

最小选型是本地 command hook 读写既有本机收件／回报记录，网络轮询和补传放到独立 collector：

| 原生事件 | 建议动作 | 明确禁止的推论 |
| --- | --- | --- |
| `SessionStart`／`UserPromptSubmit` | 查询本机 inbox，给出可信计数；已绑定时附最小 job ID／hash | 不据此发起新执行；不把 mailbox content 原文提升为 developer 指令 |
| `Stop` | 仅对已绑定 job 的 session／turn 保存结构化本地回报 spool；正常返回 `{}` | 不把 Stop 自动写成 REPORTED_COMPLETED；不直接上传末条回复或整段 transcript |
| `PreToolUse`／`PostToolUse` | 仅匹配 Bash、apply_patch 与 task-mailbox MCP，保存本地调用引用和最近观察 | 不保存 tool_input／tool_response，不默认监听所有工具，不因回报上传生成业务执行 |
| `Interrupt` | 记录中断请求及最后已知执行引用 | 不等于原执行已 STOPPED，也不自动续轮 |
| `SessionEnd` | 可做短时本地保存；不能作为唯一交付动作 | 不依赖此时的远端 MCP 调用或长时上传 |

Stop 返回 blocking decision 会以 reason 自动建立续轮提示，不能作为本轮默认行为。async command hook 在会话空闲时只于下一用户 turn 交付信息，不主动开启 turn；会话结束可能取消尚未完成的 hook。因此 collector 的生命周期必须独立于会话。[官方说明](https://learn.chatgpt.com/docs/hooks#stop)、[background 说明](https://learn.chatgpt.com/docs/hooks#run-hooks-in-the-background)

多个配置源的匹配 hook 会一起运行，异步回调完成顺序也可能不同。只配置一个明确 owner 的定义，并仍在本机账本按稳定 hook／event ID 去重。非 managed hook 必须通过原生 review/trust 流程信任精确当前 hash；本次方案评审不代替该步骤，不绕过信任检查。

additionalContext 在 Codex 中具有较高上下文权重。只放可信固定提示和 job／event／hash 这些最小关联，不直接嵌入未经验证的任务正文、外链内容或控制命令。真正任务内容仍经受控工具按数据读取与授权校验。

云端 Work／Dot 的 hooks 是另一条产品边界：enterprise managed policy 与 remote hooks 启用时，Work Cloud with local access／Dot 可由管理员在 Global `requirements.toml` 配置远端 `mcp_tool` hooks。personal accounts 和无 local access 的 Work Cloud 不使用这些企业 hooks；本地 command／插件／目录 hook 不支持云编排。当前 Dot 的企业 hook 条件仍 UNKNOWN；其 whoami／usage 成功仅证明对应只读调用，不证明 hook 权限或自动唤醒。[官方企业边界](https://learn.chatgpt.com/docs/enterprise/agent-security#hooks-in-local-computer-access-with-work-cloud)、[Hooks 说明](https://learn.chatgpt.com/docs/hooks#managed-hooks-from-requirements-toml)

## 收件、cursor 与分页的最小正确用法

当前 recipient 是**任务所属过滤**，不是事件的新投递目的地。例如任务 J 的 `sender=Dot, recipient=Mac`：其 JOB_PERSISTED、RESULT_REPORTED、CANCEL_REQUESTED、RESULT_READ_ACKNOWLEDGED 全部仍归 `jobs.recipient=Mac`。Dot 若只查 recipient=Dot，就收不到该 J 的结果事件。

第一阶段采用单 collector 拉 **不带 recipient 的全局日志**，在本地按原 job 与受信路由规则决定通知谁。当前单用户账号可读取这些事件；未来多租户不能把这个办法当成 ACL。若确实要使用 filtered feed，必须为每个固定过滤表达式保存独立 cursor，且结果承接方查询原任务所属视图；这比全局单 collector 更易用错，不作为默认。

处理顺序如下，所有 HTTP 都在 SQLite 事务之外：

1. 读出本机该 source／consumer 的已持久 cursor；向 `list_events` 发起有界请求。
2. 校验页结构、cursor 严格递增、稳定 event_id 和 next_cursor 关系。不要把游标当作连续计数，不要求相邻差值为 1。
3. 开短事务，将整页事件按 event_id 去重保存，同时将 cursor 更新到本页最后一个已持久事件；无事件时不前移。未知事件先持久为 HELD／IGNORED 并保留原因，不能无记录丢弃。
4. 提交事务后，worker 再逐项获取 job／指定版本结果、校验摘要、生成通知或等待执行准入。业务失败不回退接收 cursor，尚未处理项留在本机 inbox。
5. `has_more=true` 时在本轮页数／时长预算内继续；预算耗尽保留剩余积压，下轮接着读。`has_more=false` 不是“未来不会再有事件”。

服务当前按 `limit+1` 判断 has_more，next_cursor 是实际返回页尾；对**同一持久且未被替换的 append-only 日志**，以上消费方式可以避免“页面已取到却未入库就推进”的缺口。它不构成全系统 exactly-once。

三个必须说明的反例：

- 先查 recipient=A 得到 cursor=100，再用同 cursor 查 recipient=B，会漏 B 的 cursor≤100 事件。
- 页面取到后先保存 cursor、再入 inbox，二者间崩溃会永久跳过该页；必须一个本地事务。
- 旧数据库 cursor=10000，服务恢复到全新 cursor≤50 的库，当前 API 会继续返回空页和输入 cursor=10000，无法自动识别重建。现有 API 没有 stream_id／log epoch。第一阶段明确禁止无交接换库；迁移／恢复要人工绑定源代际并对账。若要求自动恢复保证，再增加持久 stream identity 的小型接口变更，不能伪装现有 API 已支持。

## 本机最小记录与崩溃缺口

采用一个 SQLite 文件即可，逻辑上保留三类记录：

| 记录 | 最少字段 |
| --- | --- |
| consumer | 稳定 consumer_id、源端点别名／认证主体绑定、固定 filter、last_durable_cursor |
| inbox | event_id、cursor、job_id、event_type、规范化事件摘要／原始小事件、接收时间、处理状态／原因 |
| dispatch／report | 稳定 action_id、job_id、触发 event_id、受控 handler／policy 版本、准确 native target、当前阶段、原生请求引用、取消请求位、回报版本／hash 和补传状态 |

当前实现保存 PENDING／HANDLED／HELD 的收件状态、显式绑定、会话观察与待发回报，不包含业务执行阶段。后续若另行采用执行 handler，才增加 `READY → HANDOFF_INTENT → RECEIPT`、`UNKNOWN`、`NOT_STARTED_CANCELLED`；RECEIPT 只表示明确受理，业务结果另存。取消是独立请求位，不能覆盖实际运行事实。

| 崩溃位置 | 恢复规则 |
| --- | --- |
| HTTP 取页后、本地提交前 | 原 cursor 重读，event_id 去重 |
| 页已入库、handler 尚未开始 | 从本机 PENDING／READY 恢复，先复核取消／过期／权限 |
| HANDOFF_INTENT 已提交、调用与本地回执记录之间 | 保守置 UNKNOWN，沿原目标、请求／时间及产物对账；不自动再发 prompt |
| 原生明确受理后、结果补传 ACK 丢失 | 沿已存 action／event ID 查询或重传幂等报告；不重跑业务 |
| 本地通知已展示、HANDLED 尚未保存 | 通知可能重复；用相同通知 ID 更新或允许有限重复，不宣称 exactly-once |

HANDOFF_INTENT 必须在外部动作前持久保存，然后释放事务，再做外部调用，最后另一个短事务保存回执。SQLite 事务无法跨进程原子覆盖 Herdr／模型／通知系统；这是保留 UNKNOWN 的原因。

执行回报也使用本机持久补传记录。单个通知／hook 的失败只能改变 hook delivery 状态，不能将服务已经成功的 submit_job 改写为失败。已保存到本地但未上传、上传受理、对方读到、业务采用分别记录。

## 事件映射与授权

| 信箱事件 | 本地 hook 事件／动作 | 执行边界 |
| --- | --- | --- |
| JOB_PERSISTED | job_available；取原 job 核 hash 后通知 | 默认不执行。只有本地受信 policy 与单独有效任务授权均成立，才进入 READY |
| RESULT_REPORTED | result_available；按事件携带的精确 version/hash 读取并核对 | 按 sender／report_route 的本地白名单关联；不能将任意路由文字当作发信地址或公网回调 |
| RESULT_REPORTED 且 status=UNKNOWN | reconciliation_needed | 提示沿原执行对账；不创建新任务或模型回合 |
| CANCEL_REQUESTED | cancel_requested | 未发起可在本地明确“不再启动”；运行中只走受支持停止流程，未得停止证据保留请求中／UNKNOWN |
| RESULT_READ_ACKNOWLEDGED | read_acknowledged | 只记指定版本读确认，不自动验收、删记录或交还写权 |

当前服务的单账号认证不区分 Dot、Mac、Codex、Kimi 的业务角色；sender／recipient 不能证明消息来源。通知 handler 只处理已批准路由，执行 handler 还必须校验本地 policy、项目／目录、精确目标、操作白名单、启动／执行截止时间、模型／工具预算和 owner。未知字段或缺授权只进入 HELD，不拼接 shell，不把 content 当控制命令。

执行前最后一次读取原 job 的取消／状态信息，再进入 HANDOFF_INTENT。远端取消与本地发起之间没有原子门，不能承诺取消绝对先于并发启动；取消到达后的规则和原生 stop 能力必须在执行阶段验收。

当前没有 server claim／fencing。初版限定一个 collector 和一个资源写者；独立只读消费者各有自己的 cursor。不能让两个 Mac 或两个 collector 竞争执行同一 job。租约超时只表示待核接管，不证明旧执行已停止；不凭过期 lease 自动重跑。

## 启动、离线和容量预算

以下为当前实现默认预算；首轮采用人工短窗口，常驻启用及长期行为另行记录，不据此承诺可用性：

| 项目 | 候选边界 |
| --- | --- |
| 首轮窗口 | 人工短窗口已完成；其后公子明确推进常驻，当前登录自启与真实收件证据见验证记录，不据此承诺长期可用性 |
| 空闲查询 | 每 30 秒一次，单 collector 不重叠轮次；15 分钟约 30 次 list_events 调用 |
| 页预算 | limit=25，每轮最多 4 页；有积压延后续读，不启动无限 drain |
| 请求／轮次 | 单调用超时 10 秒、每轮总时间上限 20 秒；总 RPC 上限 10 次，handler 并发为 1 |
| 错误退避 | 网络错误退避 60→120→300 秒；401／403 停受影响分支，不自动登录或换认证 |
| 本地数据 | 先只持久小事件与引用；候选数据库 32 MiB、待处理上限 500；满额停止拉取／准入并保全旧记录，不自动删除 |
| 新增业务效果 | 原型为 0 个模型回合、0 次 shell／Herdr 派发；纯通知的有限重试单独计数 |

每 30 秒无限常驻约产生 86400 次轮询/月（按 30 天），还未计 OAuth、MCP 初始化、分页、get_job 和结果读取；不能未经账号额度与实际传输测量就启用。RPC 数不等于 HTTP 请求数，必须分别记录可观察计数，内部不可读时记 UNKNOWN。

Mac 睡眠或断网时不承诺继续收件／执行。恢复后先核源身份、原 cursor、未决 HANDOFF_INTENT、取消／过期和当前 owner，再准入新动作。启动期限已过的不启动；执行期限／离线预算仅对已经另行授权的执行 handler 生效，超限停止新增动作并保存原引用，不能擅自 kill 或自动续派。第一阶段没有执行 handler，因此不需要通过执行离线预算掩盖断线。

SQLite 空间不足或本机不能持久记录时，不再产生新业务效果。日志、SQLite journal、OAuth 缓存和未来结果 spool 另外计容量；配额不等于整个目录硬限。OAuth 失效不回滚已入库事件，也不将原 job 改成业务失败。

## 验收阶段及独立 OAuth 故障

| 阶段 | 本轮应写的状态 | 退出条件 |
| --- | --- | --- |
| R0：评审与契约说明 | PASS | 公子明确授权推进，已按原方案实现；VPS MCP 契约不变 |
| R1：离线收件原型 | PASS | 台账 20 项与 collector 17 项行为测试；原子水位、重传、重启、满库回滚、未知事件与认证失败停分支 |
| R2：本机感知与原生 hook | PARTIAL | 10 项 hook 与 3 项 CLI／安装测试通过；已安装并完成 trust，真实 App 台账观察到 SessionStart／PreToolUse／PostToolUse／UserPromptSubmit。其余原生事件与 CLI 模型触发分别未验 |
| R3：Mac 桥真实收件／回报 | PASS | 实际专用 OAuth／whoami、原日志接收、原 job 摘要核对、结果安全保存与原事件 outbox 上传、当前会话经云端 App 读回核 hash／nonce 及 ACK 均已取得；指定 Dot 的结果读取归公子回报，服务独立 ACK 已直接回读；反方向另验，无模型／业务调用。人工窗口及长期状态见验证记录 |
| R4：受控执行 handler | DEFER | 明确项目、owner、target、操作／预算／取消契约；隔离 nonce；回执丢失只 UNKNOWN 对账；另获故障注入授权后测崩溃窗口 |
| R5：长期及多消费者 | DEFER | 真实额度、睡眠恢复、容量、log 重建／stream identity、接管 fencing 和长期观测均有证据后再决定 |

OAuth／原生 HTTP 发送故障与钩子能力分开，沿原客户端、App 和回调定位；云端连接回报、工具发现及 nonce 状态统一见[当前验证记录](verification.md)。不通过 hook 绕过认证，不把该故障解释为 Herdr／模型失败。Mac 桥认证与实际 MCP 收发已通过；原生 Codex 直接登录和 Kimi 登录仍沿各自入口分别处理。

当前实现的感知范围是公子选定的 Herdr 与信箱项目：新事件、取消、结果／UNKNOWN、待回传、最近会话信号和连接健康。项目路由元数据标为调用方数据，不提升为执行权。原生 trust 与四类 App 触发、专用桥 OAuth 及原 nonce 收件／补传已有证据；指定 Dot 自身读取归公子回报，服务精确读 ACK 已直接回读；Mac 发起方向仍待验证，常驻已由公子明确授权并启动，长期行为另验。此前不一致传输结果保留历史 UNKNOWN，未改代理／TLS／端点；当前成功不解释旧请求或长期稳定性。自动派发、Herdr／模型 handler、故障注入及多 Mac 抢占仍未开启。源码与操作见 [Mac 指引](../mac/README.md)，现场状态只写验证记录。
