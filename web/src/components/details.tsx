import { ArrowRight, GitBranch } from "lucide-react"
import { ApiError, pathId, scopedPath, usePagedSnapshot } from "@/lib/api"
import { traceSchema, type Role } from "@/lib/contracts"
import { href } from "@/lib/navigation"
import { DataState, elapsed, Empty, EvidenceList, Pager, reasonLabel, roleName, Section, Snapshot, Status, time } from "./display"

export function TaskDetail({ project, id, roles }: { project: string; id: string; roles: Role[] }) {
  const query = usePagedSnapshot(["project", project], scopedPath(`tasks/${pathId(id)}/trace`, project), traceSchema)
  const snapshot = query.data?.pages[0]
  const forbidden = query.error instanceof ApiError && [401, 403].includes(query.error.status)
  const task = snapshot?.data.project_id === project ? snapshot.data : null

  const events = query.data?.pages.flatMap((page) => page.data.events) ?? []
  const formal = task?.formal_status && task.evidence.find((item) => item.authority === "formal_record")

  return <>
    <DataState pending={query.isPending && !query.isPaused} error={query.error} cached={!!task} retry={() => void query.refetch()} />
    {snapshot && !task && !forbidden && <div className="panel"><Empty>该任务与当前项目不匹配。</Empty></div>}
    {snapshot && task && !forbidden && <>
      <Section title={task.title || task.id} action={task.workflow_run_id && <a className="text-link text-sm" href={href(project, "workflows", task.workflow_run_id)}><GitBranch size={14} className="mr-1 inline" aria-hidden="true" />查看工作流</a>}>
        <dl className="grid grid-cols-1 divide-border border-b border-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x">
          <div className="metric"><dt className="muted">执行方报告</dt><dd><Status value={task.reported_status} /></dd></div>
          <div className="metric"><dt className="muted">负责人验收</dt><dd><Status value={task.acceptance_status} /></dd></div>
          <div className="metric"><dt className="muted">协调待办</dt><dd><Status value={task.followup_status} /></dd>
            {task.followup_revision != null && <dd className="muted">修订 {task.followup_revision}</dd>}
            {task.followup_ref && <dd className="muted break-content">{task.followup_ref}</dd>}
          </div>
          <div className="metric"><dt className="muted">正式任务结项</dt><dd><Status value={task.formal_status} /></dd>
            {formal && <dd className="muted">{formal.source_type} · 核读 {time(formal.observed_at)}</dd>}
          </div>
        </dl>
        <div className="grid gap-4 p-5 text-sm">
          <p className="break-content"><code>{task.id}</code></p>
          <dl className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <div><dt className="muted">负责人</dt><dd>{roleName(task.owner_role_id, roles)}</dd></div>
            <div><dt className="muted">当前承接</dt><dd>{roleName(task.current_handler_role_id, roles)}</dd></div>
            <div><dt className="muted">当前步骤</dt><dd>{task.current_step_id || "未知"}</dd></div>
            <div><dt className="muted">等待对象</dt><dd>{task.waiting_on || "未知"}</dd></div>
            <div><dt className="muted">承诺交期</dt><dd>{time(task.committed_deadline_at, "未承诺")}</dd></div>
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
          <p className="muted">{[...new Set(Object.values(task.unknown_reasons).map(reasonLabel))].join(" · ") || "缺少可核对的起止时间时，耗时显示未知。"} <a className="text-link" href={href(project, "blockers")}>查看阻塞记录</a></p>
        </div>
      </Section>
      <Section title="委派链">
        {task.delegations.length ? <ol className="divide-y divide-border">
          {task.delegations.map((entry) => <li key={entry.id} className="grid gap-2 px-5 py-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <strong>{roleName(entry.from_role_id, roles, "委派方未知")}</strong>
              <ArrowRight size={15} aria-label="委派给" />
              <strong>{roleName(entry.to_role_id, roles, "接收方未知")}</strong>
              <Status value={entry.state} />
            </div>
            <div className="muted flex flex-wrap gap-x-4 gap-y-1"><span>请求 {time(entry.requested_at)}</span><span>接手 {time(entry.accepted_at, "未见接单证据")}</span></div>
            <p className="muted break-content">实际调用者 {roleName(entry.actual_caller_role_id, roles)}</p>
            {entry.evidence.length > 0 && <details><summary className="cursor-pointer text-primary">委派证据</summary><EvidenceList items={entry.evidence} /></details>}
          </li>)}
        </ol> : <Empty>暂无来源报告的委派记录。</Empty>}
      </Section>
      <Section title="事件时间线">
        {events.length ? <ol className="px-5 py-3">
          {events.map((event) => <li key={event.cursor} className="relative grid gap-1 border-l border-border py-3 pl-5 text-sm before:absolute before:top-5 before:-left-[4.5px] before:size-2 before:rounded-full before:bg-primary">
            <div className="muted">发生 {time(event.occurred_at)} · 观察 {time(event.observed_at)} · 来源 {event.source_id || "未知"}</div>
            <div className="break-content font-medium">{event.payload.summary || event.payload.latest_action || event.payload.action || `${event.entity_type} · ${event.entity_id} · ${event.event_type === "snapshot" ? "状态快照" : "活动"}`}{event.historical && <span className="muted ml-2">历史记录</span>}</div>
            {event.evidence.length > 0 && <details><summary className="cursor-pointer text-primary">事件证据</summary><EvidenceList items={event.evidence} /></details>}
          </li>)}
        </ol> : <Empty>暂无可展示的事件记录。</Empty>}
        <Pager more={query.hasNextPage} loading={query.isFetching} onMore={() => void query.fetchNextPage()} />
      </Section>
      <Section title="输入、成果与验收引用"><EvidenceList items={task.evidence} /></Section>
      <Snapshot observed={snapshot.observed_at} sources={snapshot.sources} />
    </>}
  </>
}
