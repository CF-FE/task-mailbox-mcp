# 上报进展或阻塞

1. 从原任务详情核对 `project_id`、`task_id`，按已有证据附角色绑定、实例、步骤或委派 ID。先调用 `whoami`，核对当前 OAuth 主体的 `platform_access.allowed_projects`，再从 `platform_access.reporting_sources` 选择覆盖本项目、批次全部 `entity_types` 且允许 `self_report` 的来源。使用该条目的 `source_id`。旧服务缺少这些字段或当前主体没有匹配来源时，说明上报尚未绑定，并继续可用的查询。上报沿当前 MCP OAuth 身份，采集器密钥由采集端单独配置。
2. 用 `publish_observation_batch(batch)` 提交。批次字段是 `schema_version: 1`、`batch_id`、`source_id` 和 `events`，最多 100 事件及 1 MiB。每个事件保留 `event_id`、`project_id`、`entity_type`、`entity_id`、`event_type`、`observed_at`、`payload`、`evidence`，可填已有的 `task_id`、`run_id`、`execution_id`、`step_id`、`role_binding_id`、`delegation_id`。发生时点未知时用 `occurred_at: null`。时间带时区。
3. 普通进展用 `task` 的 `activity`，记录实际进展或下一动作。持久化的 `activity` 只追加时间线，实体当前状态须另行查询。需要上报阻塞时使用当前来源允许的 `blocker` 字段，附原 `task_id`，记录实际原因、等待对象、明确处理者或 `null`、下一动作及有证据的交期。自报阻塞可查持久化回执及原任务时间线。阻塞中心及其计数要等可信来源确认后才反映当前阻塞状态。回执已确认、中心尚未显示时，说明待可信来源确认，沿原记录跟进。只有来源获准更新并能给出完整当前事实时才使用 `snapshot`。自报证据用 `authority: self_report`，附 `source_type`、原 `ref`、`observed_at`，有摘要时附 `digest`。正式状态和负责人验收沿原记录。

   `payload` 只用该实体合同声明的字段，新增私有键会被拒绝。任务、工作流、执行和委派 ID 放在事件外层，原生上下文通过 `evidence` 引用。`task` 接单进展可用 `{"reported_status":"IN_PROGRESS","next_action":"核对既有证据"}`，结果回程可用 `{"reported_status":"REPORTED_COMPLETED","next_action":"等待原协调员核收"}`。

4. 首次发送前保留原批次和事件 ID。超时、断线或结果未知时，用 `get_observation_receipt(batch_id, source_id)` 查原回执。只有确认尚无回执才可沿原 ID 原样重传。遇到 401、403、不可达或含义不明的 404 时，保持 UNKNOWN 并沿原批次核对。成功以匹配原来源和批次且 `persisted: true`、接受及重复 ID 合计精确覆盖提交事件为准。冲突沿原来源核查。

   服务明确返回 `INVALID_OBSERVATION` 且原批次回执明确为 `NOT_FOUND` 时，在原授权和单次计划限制内修正合同字段。保留拒绝原件、原批次和事件 ID、观察与发生时间及证据。更正后按同一批次读回。结果仍为 UNKNOWN 时先对账，禁止改正文重发。

有冻结的原任务、运行和执行 ID 时，在 checkpoint 事件附齐。共享 OAuth 上报的执行 ID 保留为自报。若任务已配置独立执行来源，走该宿主已批准的 HTTP 上报入口并保持原批次对账。Mac 若已安装 `observability_checkpoint.py`，用原执行的私有设置、原 session ID 和冻结的 checkpoint JSON 调用。沿用同一个输入文件对账。只在实际接单、关键产物就绪、阻塞变化或回程时上报。派发、停止及续接以调度器或宿主原生证据为准，工作流图沿单一可信来源更新。空闲、心跳或工具活动不推进业务 checkpoint。
