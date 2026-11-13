# 平台首期并行实现合同

依据 [已采纳 ADR-001](../decisions/001-task-collaboration-platform.md)。公子已授权实现／部署 win-work PostgreSQL 与监控 Web、保留既有信箱／桥、凡人原任务验证；不做数据库备份。本文固定并行开发的最小边界，变更由主控整合。

## 独立交付

| 任务 | 唯一写入范围 | 产物与依赖 |
| --- | --- | --- |
| T1 数据库 | `deployment/windows/`、`docs/verification/postgres-platform.md`；实际 Win 新 PG 服务及限定连接配置 | PG18、专用库／角色、TLS、VPS 可达性及私有连接配置。可独立执行，不操作既有 Herdr／TTS／Hindsight 服务，不做备份 |
| T2 后端 | `platform_service/`、`migrations/`、`server.py`、新增 platform 依赖清单、`tests/test_platform*`、`tests/smoke_platform*` | PostgreSQL Store、查询／接入／SSE、Web 会话认证、MCP 门面、原协议迁移程序；本机实现，生产切换由主控统一执行 |
| T3 Web | `web/` | 五个视图、真实 API 客户端、登录／错误／陈旧状态、浏览器测试；按本合同可并行开发，集成等待 T2 |
| T4 采集与 Skill | `mac/observability*`、`mac/collector.py`、`mac/codex_hook.py`、`mac/install.py`、`tests/test_observability*`、`.agents/skills/task-collaboration-monitor/`、`scripts/collect_fanren_observations.py` | 独立有界采集缓冲／上传、凡人来源适配、配套 Skill 和验证；不改原 Store 或后端，不恢复或派发原组织角色 |
| T5 集成复审 | 主控持有 README、SPEC、AGENTS、ADR、本合同、部署整合和最终验收 | 复审各分支、完整回归、部署 Web/API、真实凡人输入、权限与页面核验，汇报实际覆盖 |

任务会话的实际 ID、ACK、交期和状态在本机 `.codex/agent-state/todo.md` 维护，本文不复制执行状态。共享 checkout 按上述文件所有权工作，不擅自提交、切换分支、恢复他人修改或部署另一分支。现有依赖环境可复用；新增依赖放独立环境。

## HTTP 与 MCP

- 保持原公网 MCP 路径和 OAuth 登记。Web 建议位于 `/mailbox/ui/`；同源 API `/mailbox/api/v1/`，应用内路由 `/api/v1/`；VPS 入口正确处理原前缀。
- `GET session`：当前主体及允许项目；未登录返回 401 和可用的 `login_url`。Web 依后端提供的现有 OAuth 接入完成浏览器会话，不硬编码凭据或模拟身份。T2 固定登录／回调接口后交 T3 对接。
- `GET projects`；`GET projects/{id}/overview`；`GET projects/{id}/roles`；`GET tasks/{id}/trace`；`GET workflows/{id}`；`GET projects/{id}/blockers`。
- `GET projects/{id}/events?after={watermark}`：SSE 携带游标与受影响对象 ID，前端重新查询。首次查询的快照与 watermark 一致；服务器按项目过滤。
- 查询响应统一为 `{schema_version:1, observed_at, watermark, sources:[], data, next_cursor:null|string}`。时间用带时区 ISO8601；未知时间、耗时、交期用 null 并说明原因；错误为 `{error:{code,message,retryable},request_id}`。
- 分页默认 50、上限 200；额外精确字段由 T2 在 OpenAPI 中定稿并通知 T3／T4，不能静默漂移。
- 新增只读 MCP：`get_project_overview`、`list_role_activity`、`get_task_trace`、`get_workflow_run`、`list_blockers`。与 REST 同业务服务。原九工具参数和功能继续，usage v2 按 ADR 明确变更。
- Agent 上报共用接入合同：`publish_observation_batch` 与 `get_observation_receipt` 分别对应 batch 接入和按原 ID 对账；来源身份／项目权限由认证映射限定。普通 Agent 上报标为自报，不能自行设为原生事实或负责人验收；可信采集器采用独立来源策略。Skill 在缺少写工具或来源授权时只查询，不虚构上报成功。

## 最小展示对象

| 对象 | 首期共同字段 |
| --- | --- |
| Project | id、name、summary、owner_role_id、scope_revision、accepted_count、pending_acceptance_count、blocked_count、total_tasks（可未知）、source freshness |
| Role activity | id、name、kind、binding_id、host_ref、session_ref、task_id、observed_state、latest_action、last_observed_at、last_progress_at、elapsed_seconds（可未知）、next_action |
| Task trace | id、project_id、title、owner_role_id、reported_status、acceptance_status、formal_status、followup_status、followup_revision／来源引用、next_action、committed_deadline_at、internal_check_at、events、delegations、workflow_run_id、evidence |
| Workflow run | id、task_id、definition_version、kind（planned／observed）、nodes、edges；节点含 id、label、state、started_at、finished_at、wait_started_at、evidence |
| Blocker | id、task_id、step_id、state、reason、handler_role_id、waiting_on、opened_at、resolved_at、next_action、committed_deadline_at、evidence |
| Source | id、kind、last_observed_at、last_received_at、state（HEALTHY／STALE／UNAVAILABLE／UNKNOWN）、gap_reason |

角色种类包括 coordinator、owner、router、worker。未关联或没有活动证据要可见，不自动生成假运行。时间线展示事实来源；工作流图不得仅凭聊天顺序猜节点依赖。私有路径只返回经授权可定位的证据引用，不由浏览器请求任意本机文件。

协调待办的 followup_status 与 GitHub Project 的 formal_status 分别保存；没有核读的正式状态保持未知。取证回件、执行方报告和验收决定标明对象与来源，不扩大成整集状态。源修订只在源内比较，低权限自报不能覆盖原生／验收字段。样本只覆盖项目局部时，total_tasks 保持未知并显示观察范围，不由采集到的几项反推全项目完成率。

## 来源接入合同

`POST observations/batches` 接收 `{schema_version:1, batch_id, source_id, events:[]}`。使用来源专用认证，绑定允许的项目；不拿正文角色名当权限。事件最少包括 `event_id,project_id,entity_type,entity_id,event_type,observed_at,payload,evidence[]`，可附 `task_id,run_id,step_id,role_binding_id,delegation_id,source_revision,occurred_at`；未知发生时间为 null。最大批次 100 事件／1 MiB，事务性持久化，重复同 ID 同内容返回原回执，冲突拒绝。

响应 `{batch_id,source_id,persisted:true,accepted_event_ids:[],duplicate_event_ids:[],watermark}`；只有精确 ID 的已持久回执允许本地回收。提供按 `batch_id` 查询原回执的只读入口；结果未知沿原批次查询，不创建替代动作。各来源当前状态按其修订与明确快照更新，历史导入仅形成历史证据。

首期使用 entity_type `project/task/role/delegation/workflow/blocker/source` 和 `snapshot/activity` 两类事件。上表是展示 DTO，不代表所有字段可写入；payload 按后端输入模型白名单接收，派生计数、elapsed_seconds、last_received_at 等由服务端计算。每类有清楚的输入模型与允许字段，T2 输出的 backend-contract.json/OpenAPI 为字段核对依据，改动由主控同步。`evidence` 使用 source_type、ref、observed_at、可选 digest 和 authority；自报、原生观察、负责人决定分别标记，服务端按可信来源策略校验，不能凭上报成功提升为验收。

## Skill 的实际范围

技能主源 `.agents/skills/task-collaboration-monitor/`。实现查项目／角色／任务／工作流／阻塞、解释数据新鲜度、使用实际可用上报工具、寻找原委派及接续入口。禁止把计划或调用受理当运行／验收。人工指派工具是后续能力，本轮 Skill 只识别和说明其未启用状态。以独立场景验证技能，并在代码工具定稿后核对精确名字及参数。

## 凡人验证和交付

采用凡人原任务的真实现存记录、固定身份及当前只读观察；采集器只读原件和明确允许的表／日志元数据，不生成音频、不重新执行 R2/R3/R4、不重派 Cloud UNKNOWN 调用、不修改原协调台账。既有证据缺少历史时点或当前真实动作时准确显示未知，不能用合成数据替代现场验收。

T1–T4 完成时交文件清单、实际命令与结果、未通过项、接口变化和下一依赖。公有报告只含脱敏摘要；真实连接、凭据、原始日志及回执留指定私有交付目录。T5 核对 UI 与 Agent 查询一致，再记录原协议、数据库、真实采集、页面、Skill 各自完成情况。
