import type { ActivityEvent } from "@/lib/contracts"
import { Empty, EvidenceList, Pager, Section, Status, time } from "./display"

export function EventTimeline({ events, more, loading, onMore }: {
  events: ActivityEvent[]
  more?: boolean
  loading: boolean
  onMore: () => void
}) {
  return <Section title="事件时间线">
    {events.length ? <ol className="px-5 py-3">
      {events.map((event) => {
        const { payload } = event
        const bindingVerified = event.reporter_binding_verified
        const report = event.evidence.some(({ authority }) => authority === "self_report")
        const headline = payload.summary || payload.latest_action || payload.action ||
          `${event.entity_type} · ${event.entity_id} · ${event.event_type === "snapshot" ? "状态记录" : "活动"}`
        const fields = [
          ["阻塞原因", payload.reason],
          ["等待对象", payload.waiting_on],
          ["下一动作", payload.next_action],
          ["事件所记进展时间", payload.last_progress_at && time(payload.last_progress_at)],
        ].filter((entry): entry is [string, string] => Boolean(entry[1]))

        return <li key={event.cursor} className="relative grid min-w-0 gap-1 border-l border-border py-3 pl-5 text-sm before:absolute before:top-5 before:-left-[4.5px] before:size-2 before:rounded-full before:bg-primary">
          <div className="muted break-content">发生 {time(event.occurred_at)} · 观察 {time(event.observed_at)} · 入库 {time(event.received_at)} · 来源 {event.source_id}</div>
          <div className="flex flex-wrap items-center gap-2">
            <strong className="break-content font-medium">{headline}</strong>
            {report && <span className="muted">含执行方自报</span>}
            {event.historical && <span className="muted">历史记录</span>}
            {(payload.reported_status || payload.state) && <Status value={payload.reported_status || payload.state} />}
          </div>
          {fields.length > 0 && <dl className="grid gap-1">
            {fields.map(([label, value]) => <div key={label} className="flex min-w-0 flex-wrap gap-x-2">
              <dt className="muted">{label}</dt><dd className="break-content whitespace-pre-wrap">{value}</dd>
            </div>)}
          </dl>}
          <div className="muted break-content">事件 ID <code>{event.event_id}</code>
            {event.execution_id && <> · 执行实例 <code>{event.execution_id}</code> · {bindingVerified ? "报送凭据已核绑定" : bindingVerified === false ? "报送凭据未核绑定" : "报送凭据绑定未知"}</>}
            {event.delegation_id && <> · 委派 ID <code>{event.delegation_id}</code></>}
          </div>
          {event.evidence.length > 0 && <details><summary className="cursor-pointer text-primary">事件证据</summary><EvidenceList items={event.evidence} /></details>}
        </li>
      })}
    </ol> : <Empty>暂无可展示的事件记录。</Empty>}
    <Pager more={more} loading={loading} onMore={onMore} />
  </Section>
}
