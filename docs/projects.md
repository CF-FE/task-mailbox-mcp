# 项目协作与任务关联契约

日期：2026-11-02，Asia/Shanghai。本页定义未来项目协作的资料归属、正式任务入口和信箱关联方式；通用 tracking 示例不证明具体 GitHub 条目已核验或业务已执行。服务字段见 [SPEC](../SPEC.md)，Mac 传输与会话边界见 [Mac 指引](../mac/README.md)，实际安装和调用证据以[验证记录](verification.md)为准。

## 资料与状态各归其主

GitHub 默认组织固定为 `CF-FE`。这是组织约束，不是让所有项目共用一个仓库：各项目的仓库、Issue 和正式 Project 沿各自项目合同绑定；Task Mailbox 自有任务使用 `CF-FE/task-mailbox-mcp`。写入前读取实际 owner，个人账号、工作目录和部署副本不能替代该核对。

Task Mailbox 自身的代码、文档、配置、Skill、插件和 Issue 的唯一维护仓为 [CF-FE/task-mailbox-mcp](https://github.com/CF-FE/task-mailbox-mcp)。本地目录名、个人整合仓或历史部署来源不能改变这个目标；个人整合仓只留历史部署副本。其他项目依其原 CF-FE 仓库及 Project 任务合同维护，不把所有业务任务都放入本服务仓。

本轮新增实现已从个人整合仓固定来源 `9d36feb436264f2c0a065be7e644043a930a98dd` 接回本仓：复制时 47 个来源文件 hash 一致，保留初始基线 `7d162860c10ac2d72719522be7fe01478f4747a3`；逐文件来源见 [source-updates.json](source-updates.json)。本次接收的固定提交尚待主执行侧提交，当前工作区接收不等于本仓新提交已部署。

| 资产 | 权威位置 | 信箱与 Mac 的职责 |
| --- | --- | --- |
| 项目代码、文档、skill、plugin、配置模板 | 对应 GitHub 仓库及固定提交；沿各项目已有目录维护 | 传递仓库、版本和产物引用，不另存一套项目源码或凭据 |
| 任务约定、范围、决策、授权依据与事件记录 | 原正式 Issue，或获准 Project draft 的正文 | 关联原 Issue／draft 与 Project item；执行回执以引用补写，不能用聊天状态代替任务约定 |
| 当前 Status、Iteration 与任务排期 | 该任务所属的原正式 Project 条目 | 保存核读时的关联和排期快照；不根据收件、hook 或 ACK 自动改状态 |
| 受理、执行、停止、结果与读取事实 | 各执行系统的原生回执及产物 | 按原 job／event／目标关联，区分报告、实际读取和业务验收 |

Projects 作为统一查看入口：总览关联各项目原正式板，进入原板查看任务状态和排期。已有任务继续沿原 Issue／draft／item／Project，不为集中展示强行迁移、复制或重建旧任务；需要改变正式归属时，先明确切换点与原引用。总览中的链接或摘要不是另一份可独立修改的任务状态。

排期沿用各项目现有 Iteration，不新建统一周期或默认日期。`iteration.field_id`／`iteration_id` 必须来自对应原 Project 的实际回读；后续新任务可绑定其正式 Project，旧业务不为总览迁板。

各项目仍须逐一登记仓库、原正式板、任务来源、owner 与迭代依据，再取得对应回读；本约定和总览可用不代表所有项目已经接入。

真实总览及私有项目地址只放获准的私密配置或交付给公子的受限引用中。公开仓库、示例、日志和插件包只使用占位值；本页不加入 GitHub 凭据、自动写 API 或新的 AI 协调层。

## 权限与 Project draft 任务来源

此前 Issue create 的 403 目标是个人整合仓 `ghyghoo8/create-network-sh`，不能归为 CF-FE 仓库的授权故障，也不能推广为所有连接都缺少 Issue 写权限。仓库 metadata 中的 admin 标记不证明该 App 对特定仓库有 Issue 写权限；个人整合仓的拒绝仍按原目标独立保留。

公子明确所属仓库为 CF-FE 后，主执行侧用原 GitHub connector 回读安装范围，确认包含 `CF-FE/task-mailbox-mcp`，并以同一 create_issue 操作成功创建 [CF-FE/task-mailbox-mcp#1](https://github.com/CF-FE/task-mailbox-mcp/issues/1)，再独立 fetch 回读，随后加入原获准私有总览并回读 In Progress。全过程未修改授权、未更换凭据或 OAuth 配置。CF-FE 的这条 Issue 写路径已实际通过，个人整合仓的权限差异另行处理。

总览 Project 的读写能力已另行验证。现有 MCP 只支持关联 Issue／PR，缺少 draft 创建能力时，可用本机 `gh` 在同一个已获准、可写的 Project 中补齐 draft 操作。draft 正文仍是合法的受控任务来源，可以保留原生 draft ID、Project item、owner、范围、预算及回读依据；项目代码、文档、技能／插件和配置模板仍归对应仓库。

本轮正式任务采用上述 CF-FE Issue，旧 draft 转为历史引用并保留来源记录；历史 job 的 tracking／hash 保留，不重建执行或重派业务。新增源码闭包已经接回本仓，但本仓新提交的部署仍需独立回执；各项目也仍须逐一登记，不能由这项接收推定全部已绑定。

## 可选 tracking 与固定关联

`tracking` 在传输层是可选的 `payload` 顶层对象；Mac 关联摘要的结构校验要求 `repository`、`project_url`、`item_id`、`iteration`，以及 `issue_url`／`draft_id` 恰有一个。来源键不能同时出现或同时缺失，对象内不接受额外键。没有该字段的既有任务／data-only probe 继续兼容收件、status 和直接数据报告，不反推它已绑定 Projects。带该字段时，它与正文一起参与原 `payload_hash` 的规范化计算，提交后不能沿同一 job 偷换内容。

正常项目协作的 `bind` 入口必须有通过结构校验的 tracking；`stage-report` 再次检查该条件，防止旧的无关联 binding 生成新业务报告。直接数据报告的兼容路径不替代此业务准入。不得为补关联而修改旧 job／payload hash，或重派同一业务。

| 字段 | 契约 |
| --- | --- |
| `repository` | `OWNER/REPOSITORY`，任务对应的仓库 |
| `project_url` | `https://github.com/orgs/OWNER/projects/N` 或 `https://github.com/users/OWNER/projects/N`，N 为正整数；项目 owner 可以不同于仓库 owner |
| `item_id` | 正整数，明确引用该 Project 的条目；不能以 Issue 编号或 Project 编号代替 |
| `issue_url` | 与 `draft_id` 二选一；`https://github.com/OWNER/REPOSITORY/issues/N`，必须属于 `repository`，N 为正整数 |
| `draft_id` | 与 `issue_url` 二选一；原生返回的 `DI_` 引用，其后为 1–125 个 ASCII 字母、数字、`_` 或 `-`，总长最多 128 字符；不得推造 |
| `iteration` | 已排期对象或显式未排期对象，见下文；不能省略原因来伪装已排期 |

已排期对象为 `{"field_id": 1, "iteration_id": "REPLACE_ITERATION_ID"}`；`field_id` 为正整数，`iteration_id` 非空、最多 128 字符且无控制字符。不得把其他字段 ID、Iteration 标题或推测的日期当作实际 Iteration 绑定。

明确未排期时使用：

```json
{
  "state": "UNSCHEDULED",
  "reason": "正式 Project 的 Iteration 尚为空，等待负责人排期"
}
```

`reason` 非空、最多 512 字符；两种 Iteration 对象不能混用。数值字段不接受布尔值。URL 只接受约定的 GitHub HTTPS 结构，不接受认证信息、端口、query 或 fragment。

VPS 保存完整 payload，包括 `tracking`。Mac 只在原任务存在该字段时保存并展示对应 job 摘要的 `tracking`，同时标记 `tracking_is_caller_supplied=true`、`tracking_verification="UNVERIFIED"`。这些标记说明引用来自调用方；携带字段、通过结构检查或成功 bind，均不证明仓库可访问、item 或 draft 属于该 Project、Issue 已关联、字段或 Iteration 真实存在。Projects 的实际核读仍须单独取得依据。

有 tracking 的任务，结构化报告使用相同对象作为 `result.task_tracking`，保持原 repository／Project／item／任务来源／Iteration 关联一致。核读时间、当前状态及原生回执另行记录，不能改写 tracking 的原始快照或将它标成已验收。

## 业务派发前的核读与准入

1. 经获准的 GitHub／Projects 工具实际读取原 Issue 或受控 Project draft 正文，以及正式 Project 条目，确认仓库、item、任务来源关联及负责人；读取当前 Status、Iteration 字段和具体 Iteration。记录读取时间、执行侧与可回查依据，不能只核 URL／ID 格式或信赖本地 UNVERIFIED 摘要。
2. 在原任务中明确本次范围、目标、当前 owner、操作权限、模型／工具／查询预算、期限、取消规则和结果接收路由；确认同一待写资产只有一个控制入口与写者。已有的任务授权继续适用，引用本身不授予执行权限。
3. 缺正式 Project 或条目时明确“待绑定”及原因，不编造 URL／item。正式条目未排期时记录上述 `UNSCHEDULED` 及实际原因；是否允许未排期任务启动，依原任务与明确授权决定，不自动补造 Iteration。尚未满足本次准入条件时，保留待办，先补齐依据。
4. 准入完成后才生成业务 job，附上核读所得 tracking 及获准的执行信息；计算整个 payload 的规范化 SHA-256，持久提交并保留原 job／event。信箱接收仍不等于 Herdr／Agent 受理，实际目标与原生回执另行关联。
5. 结果沿原事件 ID 保存、补传和读回。需要更新 Issue、draft 或 Project 时，使用该资源当前获准的 GitHub 操作入口并回读写入结果；不能用另一资源的写权限覆盖本资源的拒绝，本信箱不会自动更新 GitHub。结果读取 ACK 不代替业务采用、正式验收或写权交还。

如果派发后排期或任务约定改变，沿原 Issue／draft／item 记录修订及新的核读依据，保留最初任务快照。结果或写入未知时先查原 job、event、执行引用和产物；补写缺失记录沿原引用完成，不因超时、空闲、旧状态或读 ACK 重派业务。取消请求与原生实际停止分别取证。

## 占位任务模板

本服务此前没有 `examples/` 目录惯例，任务模板集中在此页。下面展示字段落点；所有地址、标识和数字都是占位值，`payload_hash` 须在替换字段后重新计算。模板不包含任何实际执行授权，不能直接用于业务派发。

```json
{
  "job_id": "example-project-job-001",
  "payload_hash": "REPLACE_WITH_CANONICAL_PAYLOAD_SHA256",
  "payload": {
    "schema_version": 1,
    "origin_task_id": "github:OWNER/REPOSITORY#1",
    "sender": "cloud-coordinator",
    "recipient": "mac-coordinator",
    "report_route": "original-issue-and-mailbox",
    "target": {
      "repository": "OWNER/REPOSITORY",
      "workspace": "EXPLICIT_WORKSPACE",
      "session_id": "EXPLICIT_SESSION",
      "pane_id": "EXPLICIT_PANE",
      "agent": "EXPLICIT_AGENT"
    },
    "tracking": {
      "repository": "OWNER/REPOSITORY",
      "project_url": "https://github.com/orgs/OWNER/projects/1",
      "item_id": 1,
      "issue_url": "https://github.com/OWNER/REPOSITORY/issues/1",
      "iteration": {
        "field_id": 1,
        "iteration_id": "REPLACE_ITERATION_ID"
      }
    },
    "content": "仅为关联模板；先核读原正式条目，补齐本次范围、owner、预算与授权依据。"
  }
}
```

使用 Project draft 时，将模板中 `tracking.issue_url` 替换为 `tracking.draft_id`，例如占位值 `DI_REPLACE_NATIVE_DRAFT_ID`，并将来源任务／报告路由指向原 draft；其余四个必需字段不变。真实 ID 必须从该 Project 的原生回执取得，不能把此示例当成已存在的 draft。已有 job 不因后来补上 Issue 而改写或重发。

## 与 Herdr 及现有项目的衔接

Herdr 继续使用原生入口和实际 session／workspace／pane／Agent；信箱只关联资料与结果，不因 Projects 字段而获得控制权。需要 Herdr 操作技能时，引用[官方固定版本技能](https://github.com/herdrdev/herdr/blob/ee5bfb5041bb3e4a49d591c72308aae3c81ecdde/skills/herdr/SKILL.md)，固定提交为 `ee5bfb5041bb3e4a49d591c72308aae3c81ecdde`；本服务不复制技能源码，也不从安装或可发现推定实际目标已验。

各项目在自己的 GitHub 仓库维护任务说明、文档、技能／插件及配置模板；项目工具发现、GitHub 权限、原生执行、负责人验收与状态写回分别取证。新增 tracking 只补关联，不替代任何一项。
