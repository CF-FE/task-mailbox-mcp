import { GitBranch } from "lucide-react"
import { ApiError, pathId, scopedPath, usePagedSnapshot } from "@/lib/api"
import { traceSchema, type Role } from "@/lib/contracts"
import { DelegationList } from "./delegations"
import { EventTimeline } from "./event-timeline"
import { href } from "@/lib/navigation"
import { DataState, elapsed, Empty, EvidenceList, reasonLabel, roleName, Section, Snapshot, Status, time } from "./display"

export function TaskDetail({ project, id, execution, roles }: { project: string; id: string; execution: string | null; roles: Role[] }) {
  const query = usePagedSnapshot(["project", project], scopedPath(`tasks/${pathId(id)}/trace`, project, execution), traceSchema)
  const snapshot = query.data?.pages[0]
  const forbidden = query.error instanceof ApiError && [401, 403].includes(query.error.status)
  const task = snapshot?.data.project_id === project ? snapshot.data : null

  const events = query.data?.pages.flatMap((page) => page.data.events) ?? []
  const unknownReasons = Object.entries(task?.unknown_reasons ?? {})
  const relationshipConflicts = unknownReasons.filter(([field, reason]) =>
    field.startsWith("delegation:") && reason === "conflicting_source_observations")
  const detailReasons = unknownReasons.filter(([field]) => !field.startsWith("delegation:"))
    .map(([, reason]) => reasonLabel(reason))
  const formal = task?.formal_status && task.evidence.find((item) => item.authority === "formal_record")

  return <>
    <DataState pending={query.isPending && !query.isPaused} error={query.error} cached={!!task} retry={() => void query.refetch()} />
    {snapshot && !task && !forbidden && <div className="panel"><Empty>该任务与当前项目不匹配。</Empty></div>}
    {snapshot && task && !forbidden && <>
      <Section title={task.title || task.id} action={task.workflow_run_id && <a className="text-link text-sm" href={href(project, "workflows", task.workflow_run_id)}><GitBranch size={14} className="mr-1 inline" aria-hidden="true" />查看工作流</a>}>
        <dl className="grid grid-cols-1 divide-border border-b border-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x">
          <div className="metric"><dt className="muted">执行方报告</dt><dd><Status value={task.reported_status} /></dd></div>
          <div className="metric"><dt className="muted">负责人验收</dt><dd><Status value={task.acceptance_status} /></dd></div>
          <div className="metric"><dt className="muted">协调待办</dt><dd><Status value={task.followup_status?.toUpperCase() === "CLOSED" ? "已关闭" : task.followup_status} /></dd>
            {task.followup_revision != null && <dd className="muted">修订 {task.followup_revision}</dd>}
            {task.followup_ref && <dd className="muted break-content">{task.followup_ref}</dd>}
          </div>
          <div className="metric"><dt className="muted">正式任务结项</dt><dd><Status value={task.formal_status} /></dd>
            {formal && <dd className="muted">{formal.source_type} · 核读 {time(formal.observed_at)}</dd>}
          </div>
        </dl>
        <div className="grid gap-4 p-5 text-sm">
          <p className="break-content"><code>{task.id}</code></p>
          {execution && <p className="break-content"><span className="muted mr-2">实例筛选</span><code>{execution}</code>
            <a className="text-link ml-3" href={href(project, "tasks", id)}>查看整个任务</a>
          </p>}
          <dl className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <div><dt className="muted">负责人</dt><dd>{roleName(task.owner_role_id, roles)}</dd></div>
            <div><dt className="muted">当前承接</dt><dd>{roleName(task.current_handler_role_id, roles)}</dd></div>
            <div><dt className="muted">当前步骤</dt><dd>{task.current_step_id || "未知"}</dd></div>
            <div><dt className="muted">等待对象</dt><dd>{task.waiting_on || "未知"}</dd></div>
            <div><dt className="muted">接手时间</dt><dd>{time(task.accepted_at, "未见接单证据")}</dd></div>
            <div><dt className="muted">承诺交期</dt><dd>{time(task.committed_deadline_at, "未承诺")}</dd></div>
            <div><dt className="muted">最近有效进展</dt><dd>{time(task.last_progress_at)}</dd></div>
            <div><dt className="muted">内部复查点</dt><dd>{time(task.internal_check_at, "未记录")}</dd></div>
            <div className="col-span-2"><dt className="muted">下一动作</dt><dd>{task.next_action || "未知"}</dd></div>
          </dl>
          <dl className="grid grid-cols-2 gap-3 rounded-lg bg-muted/70 p-3 lg:grid-cols-5">
            {[
              ["总历时", task.elapsed_seconds],
              ["执行区间", task.execution_seconds],
              ["阻塞等待", null],
              ["接单等待", task.acceptance_wait_seconds],
              ["距最近有效进展", task.progress_age_seconds],
            ].map(([label, value]) => <div key={label}><dt className="muted">{label}</dt><dd className="font-medium">{elapsed(typeof value === "number" ? value : null)}</dd></div>)}
          </dl>
          <p className="muted">{[...new Set(detailReasons)].join(" · ") || "缺少可核对的起止时间时，耗时显示未知。"} <a className="text-link" href={href(project, "blockers")}>查看阻塞记录</a></p>
        </div>
      </Section>
      {relationshipConflicts.length > 0 && <Section title="委派关系待核">
        <ul className="grid gap-2 p-5 text-sm">
          {relationshipConflicts.map(([field, reason]) => <li key={field} className="break-content">
            委派 ID <code>{field.slice("delegation:".length)}</code> · {reasonLabel(reason)}，该关系待核。
          </li>)}
        </ul>
      </Section>}
      {task.relationships && (execution || relationshipConflicts.length > 0 || task.relationships.upstream.length + task.relationships.downstream.length > 0) ? <>
        <DelegationList title="直接上游" hasConflicts={relationshipConflicts.length > 0} entries={task.relationships.upstream} project={project} roles={roles} />
        <DelegationList title="直接下游" hasConflicts={relationshipConflicts.length > 0} entries={task.relationships.downstream} project={project} roles={roles} />
      </> : <DelegationList title="委派链" entries={task.delegations} project={project} roles={roles} />}
      <EventTimeline events={events} more={query.hasNextPage} loading={query.isFetching} onMore={() => void query.fetchNextPage()} />
      <Section title="输入、成果与验收引用"><EvidenceList items={task.evidence} /></Section>
      <Snapshot observed={snapshot.observed_at} sources={snapshot.sources} />
    </>}
  </>
}
