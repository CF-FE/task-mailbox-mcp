import { StrictMode, Suspense, lazy, useCallback, useEffect, useState } from "react"
import { createRoot } from "react-dom/client"
import { QueryCache, QueryClient, QueryClientProvider, useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query"
import { Activity, AlertTriangle, Boxes, FileClock, GitBranch, LayoutList, LogIn, Radio, RefreshCw } from "lucide-react"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { DataState, Empty, Pager, Snapshot } from "@/components/display"
import { Blockers, Overview, RoleActivity, TaskDirectory } from "@/components/lists"
import { TaskDetail } from "@/components/details"
import { ApiError, apiBase, get, projectPath, usePagedSnapshot, usePages } from "@/lib/api"
import { overviewSchema, projectSchema, roleSchema, sessionSchema } from "@/lib/contracts"
import type { Project } from "@/lib/contracts"
import { useLiveUpdates } from "@/lib/live"
import { href, pages, useRoute } from "@/lib/navigation"
import type { Page } from "@/lib/navigation"
import "./style.css"

const Workflow = lazy(() => import("@/components/workflow"))
const nav = {
  overview: { title: "项目总览", icon: LayoutList },
  roles: { title: "角色活动", icon: Activity },
  tasks: { title: "任务详情", icon: FileClock },
  workflows: { title: "工作流运行图", icon: GitBranch },
  blockers: { title: "阻塞中心", icon: AlertTriangle },
}

const client = new QueryClient({
  queryCache: new QueryCache({
    onError(error, query) {
      if (query.queryKey[0] !== "session" && error instanceof ApiError && error.status === 401) {
        void client.invalidateQueries({ queryKey: ["session"] })
      }
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 20_000,
      retry: (count, error) => count < 1 && (!(error instanceof ApiError) || error.retryable),
      networkMode: "always",
    },
  },
})

function Brand() {
  return <div className="flex items-center gap-3">
    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground"><Boxes size={19} aria-hidden="true" /></span>
    <div><span className="block text-sm font-semibold">协作监控</span><span className="block text-xs text-muted-foreground">Task Mailbox</span></div>
  </div>
}

function loginLink(loginUrl?: string) {
  if (!loginUrl) return undefined
  try {
    const url = new URL(loginUrl, window.location.origin)
    if (url.origin === window.location.origin && url.pathname === apiBase + "session/login") return url.href
  } catch {
    return undefined
  }
}

function ProjectView({ project, page, id }: { project: Project; page: Page; id: string | null }) {
  const qc = useQueryClient()
  const overview = usePagedSnapshot(["project", project.id], projectPath(project.id) + "/overview", overviewSchema)
  const roles = usePages(["project", project.id], projectPath(project.id) + "/roles", roleSchema)
  const [streamAuthError, setStreamAuthError] = useState<ApiError>()
  const onAuthFailure = useCallback((error: ApiError) => {
    setStreamAuthError(error)
    void qc.invalidateQueries({ queryKey: ["session"] })
  }, [qc])
  const snapshot = overview.data?.pages[0]
  const connection = useLiveUpdates(project.id, snapshot?.watermark, onAuthFailure)
  const forbidden = overview.error instanceof ApiError && [401, 403].includes(overview.error.status)
  const rolesForbidden = roles.error instanceof ApiError && [401, 403].includes(roles.error.status)
  const roleItems = rolesForbidden ? [] : roles.data?.pages.flatMap((result) => result.data) ?? []
  const tasks = overview.data?.pages.flatMap((result) => result.data.tasks) ?? []
  const refreshing = useIsFetching({ queryKey: ["project", project.id] }) > 0
  const pager = <Pager more={overview.hasNextPage} loading={overview.isFetching} onMore={() => void overview.fetchNextPage()} />

  return <main id="main" className="mx-auto grid w-full max-w-[1440px] min-w-0 gap-5 px-4 py-6 sm:px-8 sm:py-8">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="grid min-w-0 gap-2">
        <div className="muted break-content flex flex-wrap items-center gap-2">
          <span>{snapshot?.data.name || project.name || project.id}</span>
          {id && <><span aria-hidden="true">/</span><a className="text-link" href={href(project.id, page)}>返回列表</a></>}
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">{nav[page].title}</h1>
        {page === "overview" && (snapshot?.data.summary || project.summary) && <p className="muted break-content">{snapshot?.data.summary || project.summary}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span role="status" className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm">
          <Radio size={15} aria-hidden="true" className={connection === "live" ? "text-primary" : "text-muted-foreground"} />
          {connection === "live" ? "实时已连接" : connection === "offline" ? "实时连接中断" : "等待实时连接"}
        </span>
        <Button variant="outline" type="button" disabled={refreshing} onClick={() => void qc.invalidateQueries({ queryKey: ["project", project.id] })}>
          <RefreshCw aria-hidden="true" />刷新
        </Button>
      </div>
    </header>
    {streamAuthError && <Login error={streamAuthError} retry={() => window.location.reload()} />}
    {!streamAuthError && <>
      {connection === "offline" && <Alert className="border-[#e6ceaa] bg-[#fff7e9]">
        <AlertTriangle aria-hidden="true" /><AlertTitle>实时连接中断</AlertTitle>
        <AlertDescription>页面会尝试重连。当前快照可能已陈旧，可手动刷新。</AlertDescription>
      </Alert>}
      {(page !== "roles" || forbidden) && <DataState pending={overview.isPending} error={overview.error} cached={!!snapshot} retry={() => void overview.refetch()} />}
      {!forbidden && (page === "roles" ? <RoleActivity project={project.id} tasks={tasks} /> :
        page === "blockers" ? <Blockers project={project.id} roles={roleItems} tasks={tasks} /> :
        page === "tasks" && id ? <TaskDetail key={id} project={project.id} id={id} roles={roleItems} /> :
        page === "workflows" && id ? <Suspense fallback={<DataState pending error={null} retry={() => {}} />}>
          <Workflow key={id} project={project.id} id={id} />
        </Suspense> :
        snapshot && <>
          {page === "overview" ? <Overview project={snapshot.data} tasks={tasks} roles={roleItems} observed={snapshot.observed_at} pager={pager} />
            : <TaskDirectory key={page} project={project.id} tasks={tasks} pager={pager} workflow={page === "workflows"} />}
          <Snapshot observed={snapshot.observed_at} sources={snapshot.sources} />
        </>
      )}
    </>}
  </main>
}

function Catalog({ viewerLabel, allowed }: { viewerLabel: string; allowed: string[] }) {
  const catalog = usePages(["catalog"], "projects", projectSchema)
  const route = useRoute()
  const forbidden = catalog.error instanceof ApiError && [401, 403].includes(catalog.error.status)
  const projects = catalog.data?.pages.flatMap((snapshot) => snapshot.data).filter((project) => allowed.includes(project.id)) ?? []
  const selected = projects.find((project) => project.id === route.project) ?? (route.project ? undefined : projects[0])
  const more = catalog.hasNextPage
  const selectValue = selected?.id ?? ""
  const requestedMissing = route.project && !selected

  return <>
    <a href="#main" className="sr-only focus:not-sr-only" onClick={(event) => {
      event.preventDefault()
      const main = document.querySelector("main")
      main?.setAttribute("tabindex", "-1")
      main?.focus()
    }}>跳到主要内容</a>
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-x-6 gap-y-3 px-4 py-3 sm:px-8">
        <Brand />
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <label className="flex min-w-0 items-center gap-2 text-sm"><span className="text-muted-foreground">项目</span>
            <select aria-label="项目" className="field min-w-0 max-w-[min(230px,60vw)]" value={selectValue} disabled={!projects.length || forbidden} onChange={(event) => {
              window.location.hash = href(event.target.value)
            }}>
              {!selected && <option value="">{projects.length ? "选择项目" : "暂无可选项目"}</option>}
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name || project.id}</option>)}
            </select>
          </label>
          {more && <Button variant="outline" size="sm" onClick={() => void catalog.fetchNextPage()} disabled={catalog.isFetching}>加载更多项目</Button>}
          <span className="muted break-content">{viewerLabel}</span>
        </div>
      </div>
      {selected && !forbidden && <nav aria-label="监控视图" className="mx-auto flex max-w-[1440px] gap-1 overflow-x-auto px-4 pb-2 sm:px-8">
        {pages.map((page) => {
          const { title, icon: Icon } = nav[page]
          return <a key={page} className="nav-link" href={href(selected.id, page)} aria-current={page === route.page ? "page" : undefined}>
            <Icon size={16} aria-hidden="true" />{title}
          </a>
        })}
      </nav>}
    </header>
    {(catalog.isPending || catalog.error || !selected) && <div className="mx-auto grid max-w-[1440px] gap-4 px-4 pt-5 sm:px-8">
      <DataState pending={catalog.isPending} error={catalog.error} cached={!!selected} retry={() => void catalog.refetch()} />
      {catalog.data && !selected && !forbidden && <div className="panel"><Empty>{requestedMissing
        ? (more ? "当前页未找到该项目，可加载更多项目继续查找。" : "该项目不可用，请从项目列表选择。")
        : "当前会话暂无可查看的项目。"}</Empty></div>}
    </div>}
    {selected && !forbidden && <ProjectView key={selected.id} project={selected} page={route.page} id={route.id} />}
  </>
}

function Login({ error, retry }: { error: ApiError; retry: () => void }) {
  const url = loginLink(error.loginUrl)
  return <div className="panel grid max-w-xl gap-3 p-6">
    <h1 className="text-xl font-semibold">登录协作监控</h1>
    <p className="muted">{url ? "使用获准的 GitHub 账号登录后查看项目。" : "登录入口暂不可用，请重新读取会话。"}</p>
    <div>{url ? <Button asChild><a href={url}><LogIn aria-hidden="true" />使用 GitHub 登录</a></Button> : <Button variant="outline" onClick={retry}>重新读取</Button>}</div>
  </div>
}

function App() {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: ({ signal }) => get("session", sessionSchema, signal),
    refetchInterval: 60_000,
  })
  const denied = session.error instanceof ApiError && [401, 403].includes(session.error.status)
  const viewer = session.data
  const qc = useQueryClient()

  // Unmount the protected views before dropping their cached query results.
  useEffect(() => {
    if (denied) {
      qc.removeQueries({ queryKey: ["project"] })
      qc.removeQueries({ queryKey: ["catalog"] })
    }
  }, [denied, qc])

  return <>
    {import.meta.env.DEV && import.meta.env.MODE === "fixture" && <p className="border-b border-[#e6ceaa] bg-[#fff7e9] px-4 py-2 text-center text-sm text-[#805209]">开发 fixture · 合成测试数据</p>}
    {viewer && !denied
      ? <Catalog key={viewer.mode === "local" ? "local" : viewer.subject}
          viewerLabel={viewer.mode === "local" ? "本机只读" : viewer.github_login}
          allowed={viewer.allowed_projects} />
      : <div className="mx-auto grid max-w-3xl gap-6 px-4 py-10 sm:px-8">
        <Brand />
        {session.error instanceof ApiError && session.error.status === 401
          ? <Login error={session.error} retry={() => void session.refetch()} />
          : <DataState pending={session.isPending} error={session.error} retry={() => void session.refetch()} />}
      </div>}
  </>
}

createRoot(document.getElementById("root")!).render(
  <StrictMode><QueryClientProvider client={client}><App /></QueryClientProvider></StrictMode>,
)
