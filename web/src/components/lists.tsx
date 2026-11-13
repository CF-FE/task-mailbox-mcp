import { useState } from "react"
import { ArrowRight, Search } from "lucide-react"
import { ApiError, projectPath, usePages } from "@/lib/api"
import { blockerSchema, roleSchema } from "@/lib/contracts"
import type { Project, Role, TaskSummary } from "@/lib/contracts"
import { href } from "@/lib/navigation"
import { Badge } from "./ui/badge"
import { DataState, elapsed, Empty, EvidenceList, interval, Pager, Section, Snapshot, Status, stateLabel, reasonLabel, roleName, time } from "./display"

const roleKinds: Record<string, string> = { coordinator: "协调员", owner: "负责人", router: "路由", worker: "Worker" }
const searchable = (value: string) => value.trim().toLocaleLowerCase("zh-CN")

export function Overview({ project, tasks, roles, observed, pager }: {
  project: Project; tasks: TaskSummary[]; roles: Role[]; observed: string; pager: React.ReactNode
}) {
  const metrics = [
    { label: project.total_tasks == null ? "已加载工作项" : "纳入范围", value: project.total_tasks ?? tasks.length },
    { label: "负责人已验收", value: project.accepted_count },
    { label: "待负责人验收", value: project.pending_acceptance_count },
    { label: "已确认阻塞工作项", value: project.blocked_count, link: href(project.id, "blockers") },
  ]
  return <>
    <div className="grid gap-x-8 gap-y-2 text-sm sm:flex sm:flex-wrap">
      <span><span className="muted mr-2">负责人</span>{roleName(project.owner_role_id, roles)}</span>
      <span><span className="muted mr-2">范围版本</span>{project.scope_revision ?? "未知"}{project.total_tasks == null && <span className="muted"> · 范围总数未知</span>}</span>
    </div>
    <dl className="panel grid grid-cols-2 divide-border sm:grid-cols-4 sm:divide-x">
      {metrics.map(({ label, value, link }) => <div className="metric" key={label}>
        <dt className="text-xs font-medium text-muted-foreground">{link
          ? <a href={link} className="text-link">{label}<ArrowRight size={13} aria-hidden="true" className="ml-1 inline" /></a> : label}</dt>
        <dd className="text-2xl font-semibold tracking-tight">{value ?? "未知"}</dd>
        {link && <dd>
          <Badge variant="outline" className="badge" data-tone={project.unresolved_blocker_count === 0 ? "neutral" : "warning"}>
            待核阻塞 {project.unresolved_blocker_count ?? "未知"}
          </Badge>
        </dd>}
      </div>)}
    </dl>
    <Section title="任务进展" action={<a className="text-link text-sm" href={href(project.id, "tasks")}>查找任务 <ArrowRight size={14} className="ml-1 inline" aria-hidden="true" /></a>}>
      {tasks.length ? <div className="table-shell"><table className="overview-table min-w-[760px]">
        <thead><tr>{["任务 / 当前步骤", "当前承接", "等待对象 / 下一动作", "执行耗时 / 距最近进展"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{tasks.map((task) => <tr key={task.id}>
          <td><a className="text-link" href={href(project.id, "tasks", task.id)}>{task.title || task.id}</a><div className="muted mt-1">{task.current_step_id || "步骤未知"}</div></td>
          <td data-label="当前承接">{roleName(task.current_handler_role_id, roles)}</td>
          <td data-label="等待对象 / 下一动作">{task.waiting_on || "等待对象未知"}<div className="muted mt-1">{task.next_action || "下一动作未知"}</div></td>
          <td data-label="执行耗时 / 距最近进展">{elapsed(interval(task.started_at, task.finished_at))}<div className="muted mt-1">{elapsed(interval(task.last_progress_at, observed))}</div></td>
        </tr>)}</tbody>
      </table></div> : <Empty>当前项目快照暂无任务摘要。</Empty>}
      {pager}
    </Section>
  </>
}

export function TaskDirectory({ project, tasks, pager, workflow = false }: { project: string; tasks: TaskSummary[]; pager: React.ReactNode; workflow?: boolean }) {
  const [search, setSearch] = useState("")
  const query = searchable(search)
  const candidates = workflow ? tasks.filter((task) => task.workflow_run_id) : tasks
  const visible = candidates.filter((task) => searchable(`${task.title || task.id} ${task.id}`).includes(query))
  return <Section title={workflow ? "可查看的工作流" : "任务目录"} action={
    <label className="relative"><Search className="pointer-events-none absolute left-3 top-3 text-muted-foreground" size={16} aria-hidden="true" />
      <input type="search" className="field pl-9" placeholder="按任务名或 ID 查找" aria-label="按任务名或 ID 查找" value={search} onChange={(event) => setSearch(event.target.value)} />
    </label>
  }>
    {visible.length ? <ul className="divide-y divide-border">
      {visible.map((task) => <li key={task.id}>
        <a href={href(project, workflow ? "workflows" : "tasks", workflow ? task.workflow_run_id : task.id)} className="flex min-h-14 items-center justify-between gap-4 px-5 py-3 hover:bg-muted">
          <span className="min-w-0"><strong className="break-content text-sm">{task.title || task.id}</strong><span className="muted break-content block">{workflow ? task.workflow_run_id : task.id}</span></span>
          <ArrowRight size={16} className="shrink-0 text-primary" aria-hidden="true" />
        </a>
      </li>)}
    </ul> : <Empty>{query ? "没有匹配的记录。" : workflow ? "当前项目暂无可定位的工作流运行。" : "当前项目快照暂无任务摘要。"}</Empty>}
    {pager}
  </Section>
}

export function RoleActivity({ project, tasks }: { project: string; tasks: TaskSummary[] }) {
  const query = usePages(["project", project], projectPath(project) + "/roles", roleSchema)
  const [kind, setKind] = useState("")
  const snapshot = query.data?.pages[0]
  const forbidden = query.error instanceof ApiError && [401, 403].includes(query.error.status)
  const roles = (query.data?.pages.flatMap((page) => page.data) ?? []).filter((role) => !kind || role.kind === kind)

  return <>
    <DataState pending={query.isPending && !query.isPaused} error={query.error} cached={!!snapshot} retry={() => void query.refetch()} />
    {snapshot && !forbidden && <>
      <Section title="角色活动记录" action={<label className="flex items-center gap-2 text-sm">
        角色类别
        <select className="field" value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="">全部</option>
          {Object.entries(roleKinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>}>
        {roles.length ? <div className="table-shell"><table className="min-w-[850px]">
          <thead><tr>{["角色 / 证据", "当前任务", "最近动作 / 下一动作", "接手时间 / 执行耗时", "最近观察 / 最近进展"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{roles.map((role) => <tr key={role.id}>
            <td><strong>{role.name || role.id}</strong><div className="muted">{roleKinds[role.kind ?? ""] ?? role.kind ?? "类别未知"}</div>
              <div className="mt-1 flex flex-wrap gap-1"><Status value={role.observed_state} /></div>
              {role.binding_id || role.host_ref || role.session_ref ? <details className="muted mt-1">
                <summary className="cursor-pointer">身份引用</summary>
                <div className="break-content">绑定 {role.binding_id ?? "未知"}<br />宿主 {role.host_ref ?? "未知"}<br />会话 {role.session_ref ?? "未知"}</div>
              </details> : <div className="muted">绑定未知</div>}
              {role.evidence.length > 0 && <details className="muted mt-1"><summary className="cursor-pointer">角色证据</summary><EvidenceList items={role.evidence} /></details>}
            </td>
            <td>{role.task_id ? <a className="text-link" href={href(project, "tasks", role.task_id)}>{tasks.find((task) => task.id === role.task_id)?.title || role.task_id}</a> : "暂无关联任务"}</td>
            <td>{role.latest_action || "动作未知"}<div className="muted mt-1">{role.next_action || "下一动作未知"}</div></td>
            <td>{time(role.accepted_at)}<div className="muted mt-1">{elapsed(role.elapsed_seconds)}{role.elapsed_seconds == null && role.unknown_reasons.elapsed_seconds && <div>{reasonLabel(role.unknown_reasons.elapsed_seconds)}</div>}</div></td>
            <td>{time(role.last_observed_at)}<div className="muted mt-1">{time(role.last_progress_at)}</div></td>
          </tr>)}</tbody>
        </table></div> : <Empty>{kind ? "已加载记录中没有该类别。" : "暂无角色活动记录。"}</Empty>}
        <Pager more={query.hasNextPage} loading={query.isFetching} onMore={() => void query.fetchNextPage()} />
      </Section>
      <Snapshot observed={snapshot.observed_at} sources={snapshot.sources} />
    </>}
  </>
}

export function Blockers({ project, roles, tasks }: { project: string; roles: Role[]; tasks: TaskSummary[] }) {
  const query = usePages(["project", project], projectPath(project) + "/blockers", blockerSchema)
  const [filter, setFilter] = useState("")
  const snapshot = query.data?.pages[0]
  const forbidden = query.error instanceof ApiError && [401, 403].includes(query.error.status)
  const all = query.data?.pages.flatMap((page) => page.data) ?? []
  const statuses = [...new Set(all.map((item) => item.state?.toUpperCase() || "UNKNOWN"))].sort()
  const blockers = all.filter((blocker) => !filter || (blocker.state?.toUpperCase() || "UNKNOWN") === filter)

  return <>
    <DataState pending={query.isPending && !query.isPaused} error={query.error} cached={!!snapshot} retry={() => void query.refetch()} />
    {snapshot && !forbidden && <>
      <Section title="阻塞记录" action={<label className="flex items-center gap-2 text-sm">
        阻塞状态
        <select className="field" value={filter} onChange={(event) => setFilter(event.target.value)}>
          <option value="">全部</option>
          {statuses.map((state) => <option key={state} value={state}>{stateLabel(state)}</option>)}
        </select>
      </label>}>
        {blockers.length ? <ul className="divide-y divide-border">
          {blockers.map((blocker) => <li key={blocker.id} className="grid gap-3 px-5 py-4">
            <div className="flex flex-wrap items-center gap-2"><Status value={blocker.state} />
              <h3 className="break-content text-sm font-semibold">{blocker.reason || "阻塞原因未知"}</h3></div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              {blocker.task_id ? <a className="text-link" href={href(project, "tasks", blocker.task_id)}>{tasks.find((task) => task.id === blocker.task_id)?.title || blocker.task_id}</a> : <span className="muted">关联任务未知</span>}
              {blocker.step_id && <span className="muted break-content">步骤 {blocker.step_id}</span>}
              <span className="muted">打开 {time(blocker.opened_at)}{blocker.resolved_at && ` · 解除 ${time(blocker.resolved_at)}`}</span>
            </div>
            <dl className="grid grid-cols-2 gap-3 rounded-lg bg-muted/65 p-3 text-sm lg:grid-cols-4">
              <div><dt className="muted">处理人</dt><dd>{roleName(blocker.handler_role_id, roles)}</dd></div>
              <div><dt className="muted">等待对象</dt><dd>{blocker.waiting_on || "未知"}</dd></div>
              <div><dt className="muted">等待耗时</dt><dd>{elapsed(blocker.elapsed_seconds ?? interval(blocker.opened_at, blocker.resolved_at))}{blocker.elapsed_seconds == null && blocker.unknown_reasons.elapsed_seconds && <div className="muted">{reasonLabel(blocker.unknown_reasons.elapsed_seconds)}</div>}</dd></div>
              <div><dt className="muted">承诺交期</dt><dd>{time(blocker.committed_deadline_at, "未承诺")}</dd></div>
            </dl>
            <p className="break-content text-sm"><span className="muted mr-2">下一动作</span>{blocker.next_action || "未知"}</p>
            {blocker.evidence.length > 0 && <details className="text-sm"><summary className="cursor-pointer text-primary">查看证据（{blocker.evidence.length}）</summary><EvidenceList items={blocker.evidence} /></details>}
          </li>)}
        </ul> : <Empty>{filter ? "已加载记录中没有该状态。" : "暂无阻塞记录。"}</Empty>}
        <Pager more={query.hasNextPage} loading={query.isFetching} onMore={() => void query.fetchNextPage()} />
      </Section>
      <Snapshot observed={snapshot.observed_at} sources={snapshot.sources} />
    </>}
  </>
}
