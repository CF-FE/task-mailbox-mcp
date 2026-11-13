import { AlertCircle, Clock3, Database, ExternalLink, RefreshCw } from "lucide-react"
import type { ReactNode } from "react"
import { ApiError } from "@/lib/api"
import type { Evidence, Role, Source } from "@/lib/contracts"
import { Alert, AlertDescription, AlertTitle } from "./ui/alert"
import { Badge } from "./ui/badge"
import { Button } from "./ui/button"
import { Skeleton } from "./ui/skeleton"

const statuses: Record<string, [string, string]> = {
  UNKNOWN: ["未知", "neutral"],
  HEALTHY: ["正常", "success"],
  STALE: ["陈旧", "warning"],
  UNAVAILABLE: ["不可用", "danger"],
  ACTIVE: ["活动中", "active"],
  IDLE: ["空闲", "neutral"],
  RUNNING: ["执行中", "active"],
  WAITING: ["等待中", "warning"],
  BLOCKED: ["阻塞中", "warning"],
  OPEN: ["未解除", "warning"],
  RESOLVED: ["已解除", "success"],
  FAILED: ["失败", "danger"],
  REPORTED_COMPLETED: ["报告完成", "success"],
  REPORTED_FAILED: ["报告失败", "danger"],
  ACCEPTED: ["已验收", "success"],
  REJECTED: ["验收未通过", "danger"],
  PENDING_ACCEPTANCE: ["待验收", "warning"],
  PENDING: ["待处理", "neutral"],
  REVIEW_REQUIRED: ["需核读回件", "warning"],
  IN_PROGRESS: ["进行中", "active"],
  CLOSED: ["已结项", "success"],
  COMPLETED: ["已完成", "success"],
  SUCCEEDED: ["成功", "success"],
  DONE: ["已完成", "success"],
  NOT_STARTED: ["未开始", "neutral"],
  NOT_REPORTED: ["未报告", "neutral"],
  NOT_ACCEPTED: ["未验收", "neutral"],
  NOT_CLOSED: ["未结项", "neutral"],
  PLANNED: ["计划", "neutral"],
  CANCELLED: ["已取消", "neutral"],
  UNBOUND: ["未关联", "neutral"],
}

export function stateLabel(value?: string | null) {
  const normalized = value?.toUpperCase()
  return (normalized && statuses[normalized]?.[0]) || value || "未知"
}

export function Status({ value }: { value?: string | null }) {
  const label = stateLabel(value)
  const tone = statuses[value?.toUpperCase() ?? ""]?.[1] ?? "neutral"
  return <Badge variant="outline" className="badge max-w-full whitespace-normal" data-tone={tone}>{label}</Badge>
}

const secondsFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 3 })
const clock = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
})
export function time(value?: string | null, fallback = "未知") {
  return value ? clock.format(new Date(value)) : fallback
}

export function elapsed(value?: number | null) {
  if (value == null) return "未知"
  if (value < 60) return `${secondsFormat.format(value)} 秒`
  const minutes = Math.floor(value / 60)
  if (minutes < 60) return `${minutes} 分 ${Math.floor(value % 60)} 秒`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时 ${minutes % 60} 分`
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`
}

export function interval(from?: string | null, to?: string | null) {
  if (!from || !to) return null
  const seconds = (Date.parse(to) - Date.parse(from)) / 1000
  return seconds >= 0 ? seconds : null
}

export function reasonLabel(reason: string) {
  const labels: Record<string, string> = {
    missing_or_inconsistent_time_evidence: "起止时间缺失或不一致",
    no_commitment_recorded: "未记录承诺交期",
    no_time_commitment_recorded: "未记录约定时间",
    no_live_observations: "暂无实时观察",
    conflicting_source_observations: "来源记录相互冲突",
    source_freshness_window_exceeded: "来源超过约定刷新时间",
  }
  return labels[reason] ?? reason
}

export function roleName(id: string | null | undefined, roles: Role[], fallback = "未知") {
  return id ? roles.find((role) => role.id === id)?.name || id : fallback
}

export function DataState({ pending, error, cached, retry }: {
  pending?: boolean
  error: Error | null
  cached?: boolean
  retry: () => void
}) {
  if (pending) return <div className="panel grid gap-4 p-5" role="status" aria-label="加载中">
    <Skeleton className="h-6 w-1/3 bg-muted" /><Skeleton className="h-24 w-full bg-muted" />
  </div>
  if (!error) return null
  const status = error instanceof ApiError ? error.status : undefined
  const blocked = status === 401 || status === 403
  const title = blocked ? (status === 401 ? "会话已失效" : "无权查看此内容") : cached ? "数据刷新失败，当前快照已陈旧" : "暂时无法读取数据"
  const message = error instanceof ApiError ? error.message : "连接中断。恢复连接后可重新读取。"
  return <Alert className="border-[#e6ceaa] bg-[#fff7e9]">
    <AlertCircle aria-hidden="true" />
    <AlertTitle>{title}</AlertTitle>
    <AlertDescription>
      <span>{message}</span>
      {error instanceof ApiError && error.requestId && <span>请求编号 {error.requestId}</span>}
      <Button type="button" variant="outline" size="sm" onClick={retry} className="mt-1 min-h-9">
        <RefreshCw aria-hidden="true" />重新读取
      </Button>
    </AlertDescription>
  </Alert>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-5 py-8 text-sm text-muted-foreground">{children}</div>
}

export function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return <section className="panel">
    <header className="panel-head"><h2 className="font-semibold">{title}</h2>{action}</header>
    {children}
  </section>
}

export function Snapshot({ observed, sources }: { observed: string; sources: Source[] }) {
  const stale = sources.filter((source) => source.state?.toUpperCase() !== "HEALTHY").length
  return <details className="panel">
    <summary className="flex min-h-11 cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3 text-sm">
      <Database size={15} aria-hidden="true" />
      <span className="font-semibold">数据来源</span>
      <span className="text-muted-foreground">{sources.length ? `${sources.length} 个来源` : "来源未知"}</span>
      {stale > 0 && <Badge variant="outline" className="badge" data-tone="warning">{stale} 个来源需核对</Badge>}
      <span className="text-muted-foreground sm:ml-auto"><Clock3 size={13} className="mr-1 inline" aria-hidden="true" />快照 {time(observed)} · 北京时间</span>
    </summary>
    {sources.length ? <ul className="grid gap-3 border-t border-border p-5 text-sm">
      {sources.map((source) => <li key={source.id} className="grid gap-1">
        <div className="flex flex-wrap items-center gap-2"><strong className="break-content">{source.id}</strong><Status value={source.state} /><span className="muted">{source.kind}</span></div>
        <div className="muted">最近观察 {time(source.last_observed_at)} · 最近接收 {time(source.last_received_at)}</div>
        {source.gap_reason && <p className="muted">{reasonLabel(source.gap_reason)}</p>}
      </li>)}
    </ul> : <Empty>当前快照缺少来源健康记录。</Empty>}
  </details>
}

export function EvidenceList({ items }: { items: Evidence[] }) {
  if (!items.length) return <Empty>暂无可定位的证据引用。</Empty>
  return <ul className="grid gap-4 p-5 text-sm">
    {items.map((item, index) => {
      let url: URL | undefined
      try {
        if (item.ref) {
          const parsed = new URL(item.ref)
          if (parsed.protocol === "https:" && !parsed.username && !parsed.password) url = parsed
        }
      } catch {
        // Opaque native references are displayed as text.
      }
      return <li key={index} className="grid gap-1">
        <span className="muted">{item.source_type || "来源未知"} · {time(item.observed_at)}</span>
        {item.authority && <span>{({ self_report: "执行方报告", native_observation: "原生观察", owner_decision: "负责人决定", formal_record: "正式记录" })[item.authority]}</span>}
        {url
          ? <a className="text-link break-content" href={url.href} target="_blank" rel="noreferrer">{item.ref}<ExternalLink size={12} className="ml-1 inline" aria-label="在新标签打开" /></a>
          : <code>{item.ref || "引用未知"}</code>}
      </li>
    })}
  </ul>
}

export function Pager({ more, loading, onMore }: { more?: boolean; loading: boolean; onMore: () => void }) {
  return more && <div className="border-t border-border px-5 py-3">
    <Button type="button" variant="outline" onClick={onMore} disabled={loading}>{loading ? "正在加载…" : "加载下一页"}</Button>
  </div>
}
