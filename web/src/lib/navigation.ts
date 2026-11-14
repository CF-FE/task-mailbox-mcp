import { useSyncExternalStore } from "react"

export const pages = ["overview", "roles", "tasks", "workflows", "blockers"] as const
export type Page = typeof pages[number]

function subscribe(change: () => void) {
  window.addEventListener("hashchange", change)
  return () => window.removeEventListener("hashchange", change)
}

export function useRoute() {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  const params = new URLSearchParams(hash.slice(1))
  const requestedPage = params.get("view")
  return {
    page: pages.find((page) => page === requestedPage) ?? "overview",
    project: params.get("project"),
    id: params.get("id"),
    execution: params.get("execution"),
  }
}

export function href(project: string, page: Page = "overview", id?: string | null, execution?: string | null) {
  const params = new URLSearchParams({ project, view: page })
  if (id) params.set("id", id)
  if (execution && page === "tasks" && id) params.set("execution", execution)
  return `#${params}`
}
