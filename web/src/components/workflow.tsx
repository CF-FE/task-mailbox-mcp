import { useMemo, useState } from "react"
import { Background, Controls, Handle, MarkerType, Position, ReactFlow } from "@xyflow/react"
import type { Edge, Node, NodeProps } from "@xyflow/react"
import { ApiError, pathId, scopedPath, useSnapshot } from "@/lib/api"
import { runSchema } from "@/lib/contracts"
import type { Run, Step } from "@/lib/contracts"
import { href } from "@/lib/navigation"
import { DataState, elapsed, Empty, EvidenceList, interval, Section, Snapshot, Status, time } from "./display"
import "@xyflow/react/dist/style.css"

type StepNode = Node<{ step: Step }, "step">

function StepCard({ data, selected }: NodeProps<StepNode>) {
  return <div className={`rounded-lg border bg-card p-3 text-sm ${selected ? "border-primary ring-2 ring-primary/25" : "border-border"}`}>
    <Handle type="target" position={Position.Left} isConnectable={false} />
    <strong className="line-clamp-2 break-all">{data.step.label}</strong>
    <div className="mt-2"><Status value={data.step.state} /></div>
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </div>
}

const nodeTypes = { step: StepCard }

function layout(run: Run) {
  const incoming = new Map(run.nodes.map(({ id }) => [id, 0]))
  const outgoing = new Map<string, string[]>()
  const edges: Edge[] = []
  for (const { source, target } of run.edges) {
    if (!incoming.has(source) || !incoming.has(target)) continue
    incoming.set(target, incoming.get(target)! + 1)
    const next = outgoing.get(source) ?? []
    next.push(target)
    outgoing.set(source, next)
    edges.push({ id: `edge-${edges.length}`, source, target, markerEnd: { type: MarkerType.ArrowClosed } })
  }

  const layer = new Map<string, number>()
  const queue = run.nodes.filter(({ id }) => incoming.get(id) === 0).map(({ id }) => id)
  for (const id of queue) layer.set(id, 0)
  for (let index = 0; index < queue.length; index += 1) {
    const source = queue[index]
    for (const target of outgoing.get(source) ?? []) {
      const count = incoming.get(target)! - 1
      incoming.set(target, count)
      layer.set(target, Math.max(layer.get(target) ?? 0, layer.get(source)! + 1))
      if (count === 0) queue.push(target)
    }
  }

  const cycle = new Set(run.nodes.filter(({ id }) => incoming.get(id)! > 0).map(({ id }) => id))
  const rows = new Map<number, number>()
  const unresolvedLayer = [...layer.values()].reduce((max, value) => Math.max(max, value), -1) + 1
  const nodes: StepNode[] = run.nodes.map((step) => {
    const column = cycle.has(step.id) ? unresolvedLayer : (layer.get(step.id) ?? 0)
    const row = rows.get(column) ?? 0
    rows.set(column, row + 1)
    return {
      id: step.id,
      type: "step",
      position: { x: column * 248, y: row * 126 },
      data: { step },
      width: 184,
      ariaLabel: step.label,
    }
  })
  return { nodes, edges, cyclic: cycle.size > 0, incomplete: edges.length < run.edges.length }
}

function Graph({ run, project }: { run: Run; project: string }) {
  const [selectedId, setSelectedId] = useState<string>()
  const { nodes, edges, cyclic, incomplete } = useMemo(() => layout(run), [run])
  const selected = run.nodes.find((node) => node.id === selectedId)
  const measurement = run.kind === "observed" && selected?.measured_duration_seconds != null &&
    selected.measured_duration_basis && run.evidence.some((item) => item.authority === "native_observation") &&
    selected.evidence.some((item) => item.authority === "native_observation")
    ? { seconds: selected.measured_duration_seconds, basis: selected.measured_duration_basis }
    : null
  const selectedNodes = useMemo(() => nodes.map((node) => ({ ...node, selected: node.id === selectedId })), [nodes, selectedId])

  return <>
    {(cyclic || incomplete) && <p className="mx-5 mt-4 rounded-md border border-border bg-muted p-3 text-sm">
      {cyclic && "部分来源依赖无法排出先后顺序。"}
      {incomplete && "部分依赖缺少可定位的节点，图中显示已有的依赖。"}
    </p>}
    <div className="h-[360px] bg-muted/35 sm:h-[410px]" aria-label="工作流依赖图">
      <ReactFlow<StepNode>
        nodes={selectedNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, node) => setSelectedId(node.id)}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
        minZoom={0.1}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        edgesReconnectable={false}
        deleteKeyCode={null}
        zoomOnScroll={false}
        panOnDrag
        proOptions={{ hideAttribution: true }}
        ariaLabelConfig={{ "controls.ariaLabel": "图缩放控件", "controls.zoomIn.ariaLabel": "放大", "controls.zoomOut.ariaLabel": "缩小", "controls.fitView.ariaLabel": "适配视图" }}
      >
        <Background gap={18} color="#d3ded7" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
    <div className="grid gap-3 border-t border-border p-5">
      <label className="flex flex-wrap items-center gap-2 text-sm">节点详情
        <select className="field" value={selectedId ?? ""} onChange={(event) => setSelectedId(event.target.value)}>
          <option value="">选择节点</option>
          {run.nodes.map((step) => <option key={step.id} value={step.id}>{step.label}</option>)}
        </select>
      </label>
      {selected && <>
        <div className="flex flex-wrap items-center gap-2 text-sm"><strong>{selected.label}</strong><Status value={selected.state} /></div>
        {selected.execution_id && <p className="muted break-content">执行实例 <code>{selected.execution_id}</code></p>}
        {selected.task_id && <a className="text-link w-fit text-sm" href={href(project, "tasks", selected.task_id, selected.execution_id)}>
          {selected.execution_id ? "查看实例记录" : "查看节点任务"}
        </a>}
        <dl className="grid grid-cols-2 gap-3 rounded-lg bg-muted/60 p-3 text-sm lg:grid-cols-4">
          <div><dt className="muted">开始时间</dt><dd>{time(selected.started_at)}</dd></div>
          <div><dt className="muted">结束时间</dt><dd>{time(selected.finished_at)}</dd></div>
          <div><dt className="muted">等待开始</dt><dd>{time(selected.wait_started_at)}</dd></div>
          <div>
            <dt className="muted">{!measurement ? "执行耗时" : "实测耗时"}</dt>
            <dd>{!measurement ? elapsed(interval(selected.started_at, selected.finished_at)) : `${measurement.seconds} 秒`}</dd>
          </div>
          {measurement && <div className="col-span-full">
            <dt className="muted">测量范围</dt><dd>{measurement.basis}</dd>
          </div>}
        </dl>
        <p className="text-sm"><span className="muted mr-2">上游依赖</span>{run.edges.filter((edge) => edge.target === selected.id).map((edge) => run.nodes.find((step) => step.id === edge.source)?.label ?? "节点未知").join("、") || "来源未记录"}</p>
        <details className="text-sm"><summary className="cursor-pointer text-primary">节点证据</summary><EvidenceList items={selected.evidence} /></details>
      </>}
    </div>
  </>
}

export default function Workflow({ project, id }: { project: string; id: string }) {
  const query = useSnapshot(project, scopedPath(`workflows/${pathId(id)}`, project), runSchema)
  const snapshot = query.data
  const run = snapshot?.data
  const forbidden = query.error instanceof ApiError && [401, 403].includes(query.error.status)

  return <>
    <DataState pending={query.isPending && !query.isPaused} error={query.error} cached={!!run} retry={() => void query.refetch()} />
    {snapshot && run && !forbidden && <>
      <Section title={run.kind === "planned" ? "计划图" : run.kind === "observed" ? "本次实际运行" : "运行类型未知"}
        action={run.task_id && <a href={href(project, "tasks", run.task_id)} className="text-link text-sm">返回任务</a>}>
        <p className="muted break-content px-5 py-3">运行 {run.id} · 定义版本 {run.definition_version ?? "未知"}</p>
        {run.nodes.length ? <Graph run={run} project={project} /> : <Empty>来源暂无可展示的节点。</Empty>}
      </Section>
      {run.evidence.length > 0 && <Section title="运行证据"><EvidenceList items={run.evidence} /></Section>}
      <Snapshot observed={snapshot.observed_at} sources={snapshot.sources} />
    </>}
  </>
}
