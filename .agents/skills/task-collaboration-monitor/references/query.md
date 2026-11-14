# 查询

| 要查的事实 | MCP 工具与主要参数 |
| --- | --- |
| 项目状态及统计范围 | `get_project_overview(project_id, cursor, limit)` |
| 实际角色活动与绑定 | `list_role_activity(project_id, cursor, limit)` |
| 原任务和委派／证据 | `get_task_trace(task_id, project_id, cursor, limit, execution_id)` |
| 已知工作流实例 | `get_workflow_run(run_id, project_id)` |
| 阻塞和等待对象 | `list_blockers(project_id, cursor, limit)` |

项目 ID 和任务 ID 来自既有记录。旧信箱 job 可用 `get_job` 对账，沿明确关联找平台任务。遇到同名任务先核原引用。按需用返回的 `next_cursor` 读取同一查询的下一页。工作流实例 ID 取自任务详情。

区分快照生成时间（查询外层 `observed_at`）、原证据的观察时间和角色的 `last_observed_at`。结合 `sources` 和未知原因判断新鲜度。角色最后活动、业务进展、任务验收和正式结项分别陈述。计划节点标为计划，工具事件只表示实际捕获的动作。缺任务开始、结束或等待转换时，标明对应耗时未知。内部检查点只表示复查安排，承诺交期取实际回执。

项目总览的 `blocked_count` 按任务计数。同一任务关联至少一条可信的 `OPEN` 阻塞时计一次。`unresolved_blocker_count` 按待核阻塞条目计数，包括状态未知或冲突的条目，以及缺任务关联的 `OPEN` 条目。两条 `OPEN` 阻塞分别关联两个任务且没有待核条目时，两个计数分别是 2 和 0。判断当前有无阻塞时，同时查看 `blocked_count` 和 `list_blockers`。

工具 schema 支持 `execution_id` 时可按实际执行过滤时间线和关系。查上下游先读 `relationships.upstream` 和 `relationships.downstream`，再按返回的原任务、执行 ID 下钻。工作流节点提供这两个 ID 时可沿用。检查时间线的 `reporter_binding_verified`，只用于确认报送凭据范围。另用原生派发或运行证据核对实例。共享 OAuth 自报的执行 ID 只能作为关联线索。
