import { ArrowRight } from "lucide-react"
import type { Delegation, Role } from "@/lib/contracts"
import { href } from "@/lib/navigation"
import { Empty, EvidenceList, roleName, Section, Status, time } from "./display"

const lifecycle: Record<string, string> = {
  DISPATCHED: "已派发",
  ACCEPTED: "已接单",
  CANCEL_REQUESTED: "已请求取消",
  STOPPED: "已停止",
  RESUMED: "已恢复",
  SUBMITTED: "已提交",
}

export function DelegationList({ title, entries, project, roles, hasConflicts }: {
  title: string
  entries: Delegation[]
  project: string
  roles: Role[]
  hasConflicts?: boolean
}) {
  return <Section title={title}>
    {entries.length ? <ol className="divide-y divide-border">
      {entries.map((entry) => {
        const native = entry.evidence.some(({ authority }) => authority === "native_observation")
        const endpoints = [
          { label: "上游实例", task: entry.from_task_id, execution: entry.from_execution_id },
          { label: "下游实例", task: entry.to_task_id, execution: entry.to_execution_id },
        ]
        return <li key={entry.id} className="grid gap-2 px-5 py-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <strong>{roleName(entry.from_role_id, roles, "委派方未知")}</strong>
            <ArrowRight size={15} aria-label="委派给" />
            <strong>{roleName(entry.to_role_id, roles, "接收方未知")}</strong>
            <Status value={entry.state ? lifecycle[entry.state] ?? entry.state : null} />
            <span className="muted">{native ? "原生观察" : "来源待核"}</span>
          </div>
          {(entry.from_task_id || entry.to_task_id || entry.from_execution_id || entry.to_execution_id) && <dl className="grid gap-2 sm:grid-cols-2">
            {endpoints.map(({ label, task, execution }) => <div key={label} className="min-w-0">
              <dt className="muted">{label}</dt>
              <dd className="break-content">{task
                ? <a className="text-link" href={href(project, "tasks", task, execution)}>{execution || task}</a>
                : execution || "未知"}</dd>
            </div>)}
          </dl>}
          <div className="muted flex flex-wrap gap-x-4 gap-y-1">
            <span>{native && entry.state === "DISPATCHED" ? "派发记录" : "请求"} {time(entry.requested_at)}</span>
            <span>接手 {time(entry.accepted_at, "未见接单证据")}</span>
            {entry.stopped_at && <span>停止 {time(entry.stopped_at)}</span>}
          </div>
          {entry.resumed_from_execution_id && <p className="muted break-content">恢复自 <code>{entry.resumed_from_execution_id}</code></p>}
          <p className="muted break-content">委派 ID <code>{entry.id}</code> · 实际调用者 {roleName(entry.actual_caller_role_id, roles)}</p>
          {entry.evidence.length > 0 && <details><summary className="cursor-pointer text-primary">委派证据</summary><EvidenceList items={entry.evidence} /></details>}
        </li>
      })}
    </ol> : <Empty>{hasConflicts ? "当前无可确认的关系。冲突记录见「委派关系待核」。" : "暂无可核对的委派记录。"}</Empty>}
  </Section>
}
