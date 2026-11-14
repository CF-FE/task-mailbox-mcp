import type { Plugin } from "vite"

const projectId = "fixture-project"
const runId = "fixture-run"
const t = (clock: string) => `2026-11-14T${clock}+08:00`
const now = t("10:40:00")
const taskId = "fixture-review"
const task = {
  id: taskId, project_id: projectId, title: "校验交付单元", owner_role_id: "fixture-owner",
  current_handler_role_id: "fixture-coordinator", current_step_id: "等待补充签收记录",
  reported_status: "REPORTED_COMPLETED", acceptance_status: "ACCEPTED", formal_status: null, followup_status: "PENDING",
  waiting_on: "接手团队", next_action: "核对后续接手回执", workflow_run_id: runId,
  requested_at: t("09:05:00"), accepted_at: t("09:20:00"), started_at: t("09:30:00"), finished_at: t("10:08:00"),
  last_progress_at: t("10:25:00"),
}
const otherTask = {
  id: "fixture-index", project_id: projectId, title: "整理来源索引", current_handler_role_id: "fixture-worker",
  current_step_id: "核对索引", reported_status: null, acceptance_status: "PENDING_ACCEPTANCE", formal_status: "IN_PROGRESS",
  next_action: "完成来源核读", last_progress_at: t("10:36:00"),
}
const project = {
  id: projectId, name: "交付联调演练", summary: "交付单元核验与接手记录", owner_role_id: "fixture-owner",
  scope_revision: "fixture-r3", total_tasks: null, accepted_count: 1, pending_acceptance_count: 1, blocked_count: 1, unresolved_blocker_count: 0,
}
const roles = [
  { id: "fixture-coordinator", name: "交付协调员", kind: "coordinator", task_id: taskId, observed_state: "WAITING", latest_action: "记录后续接手缺口", last_observed_at: t("10:32:00"), last_progress_at: t("10:25:00"), next_action: "等接手团队回执", elapsed_seconds: null, unknown_reasons: { elapsed_seconds: "当前承接区间缺少结束时间。" } },
  { id: "fixture-owner", name: "交付负责人", kind: "owner", task_id: taskId, observed_state: "ACTIVE", latest_action: "验收本次交付单元", last_observed_at: t("10:25:00"), last_progress_at: t("10:25:00") },
  { id: "fixture-router", name: "执行路由", kind: "router", observed_state: null, latest_action: null },
  { id: "fixture-worker", name: "索引 Worker", kind: "worker", task_id: otherTask.id, observed_state: "RUNNING", latest_action: "核读索引条目", accepted_at: t("10:31:00"), last_observed_at: t("10:38:00"), last_progress_at: t("10:36:00") },
]
const sources = [
  { id: "fixture-ledger", kind: "coordination", state: "HEALTHY", last_observed_at: t("10:32:00"), last_received_at: t("10:32:01") },
  { id: "fixture-router-source", kind: "router", state: "STALE", last_observed_at: t("09:12:00"), last_received_at: t("09:12:01"), gap_reason: "路由来源超过约定刷新时间。" },
  { id: "fixture-formal", kind: "formal", state: "UNKNOWN", gap_reason: "尚无正式状态核读记录。" },
]
const evidence = { source_type: "fixture", ref: "fixture:owner-decision", observed_at: t("10:25:00"), authority: "owner_decision" }
const blocker = {
  id: "fixture-blocker", state: "OPEN", reason: "缺少后续接手回执", task_id: taskId, step_id: "ack",
  handler_role_id: "fixture-coordinator", waiting_on: "接手团队", opened_at: t("10:25:00"),
  next_action: "核对接手记录", elapsed_seconds: null, unknown_reasons: { elapsed_seconds: "未记录等待结束时间。" },
}
const resolved = { ...blocker, id: "fixture-resolved", state: "RESOLVED", reason: "输入清单缺项已补齐", opened_at: t("09:10:00"), resolved_at: t("09:20:00"), elapsed_seconds: 600, evidence: [evidence] }
const run = {
  id: runId, task_id: taskId, kind: "observed", definition_version: "fixture-v2",
  nodes: [
    { id: "input", label: "核对输入", state: "COMPLETED", started_at: t("09:30:00"), finished_at: t("09:35:00") },
    { id: "work", label: "执行校验", state: "COMPLETED", started_at: t("09:35:00"), finished_at: t("10:08:00") },
    { id: "review", label: "核读交付记录", state: "ACCEPTED", evidence: [evidence] },
    { id: "ack", label: "等待接手回执", state: "WAITING", wait_started_at: t("10:25:00") },
  ],
  edges: [{ source: "input", target: "work" }, { source: "input", target: "review" }, { source: "work", target: "ack" }, { source: "review", target: "ack" }],
}
const events = [
  { cursor: "7", event_id: "fixture-event-7", source_id: "fixture-ledger", entity_type: "task", entity_id: taskId, event_type: "activity", observed_at: t("10:25:00"), occurred_at: t("10:25:00"), received_at: t("10:25:01"), payload: { summary: "负责人验收本次交付单元" }, evidence: [evidence] },
  { cursor: "5", event_id: "fixture-event-5", source_id: "fixture-ledger", entity_type: "task", entity_id: taskId, event_type: "activity", observed_at: t("10:08:00"), occurred_at: null, received_at: t("10:08:01"), payload: { summary: "执行方提交校验记录" }, historical: true, evidence: [] },
]

export function fixturePlugin(): Plugin {
  return {
    name: "mailbox-dev-fixture",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use("/mailbox/api/v1", (req, res) => {
        const url = new URL(req.url ?? "/", "http://fixture.invalid")
        const send = (value: unknown, status = 200) => {
          res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" })
          res.end(JSON.stringify(value))
        }
        const snapshot = (data: unknown, next_cursor: string | null = null) => send({
          schema_version: 1, observed_at: now, watermark: "8", sources, data, next_cursor,
        })
        const path = url.pathname
        if (path === "/session") return send({
          github_login: "fixture-viewer", subject: "fixture", allowed_projects: [projectId], csrf_token: "fixture",
        })
        if (path === "/projects") return snapshot([project])
        const root = `/projects/${projectId}`
        if (path === root + "/overview") return snapshot({ ...project, tasks: [task, otherTask] })
        if (path === root + "/roles") return snapshot(roles)
        if (path === root + "/blockers") return url.searchParams.has("cursor") ? snapshot([resolved]) : snapshot([blocker], "1")
        if (path === root + "/events") {
          res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" })
          res.write(": fixture stream\n\n")
          const heartbeat = setInterval(() => res.write(": heartbeat\n\n"), 5000)
          res.on("close", () => clearInterval(heartbeat))
          return
        }
        if (url.searchParams.get("project_id") === projectId) {
          if (path === `/workflows/${runId}`) return snapshot(run)
          if (path === `/tasks/${taskId}/trace`) return snapshot({
            ...task, events: url.searchParams.has("cursor") ? [events[1]] : [events[0]],
            evidence: [evidence], elapsed_seconds: null, execution_seconds: 2280, acceptance_wait_seconds: 900, progress_age_seconds: 900,
            delegations: [
              { id: "fixture-delegate", from_role_id: "fixture-coordinator", to_role_id: "fixture-owner", state: "ACCEPTED", requested_at: t("09:05:00"), accepted_at: t("09:20:00"), evidence: [evidence] },
              { id: "fixture-delegate-next", from_role_id: "fixture-coordinator", state: "UNKNOWN", requested_at: t("10:25:00") },
            ],
          }, url.searchParams.has("cursor") ? null : "7")
          if (path === `/tasks/${otherTask.id}/trace`) return snapshot({ ...otherTask, events: [], delegations: [], evidence: [] })
        }
        return send({ error: { code: "NOT_FOUND", message: "没有该记录。", retryable: false }, request_id: "fixture" }, 404)
      })
    },
  }
}
