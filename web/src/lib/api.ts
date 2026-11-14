import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { z } from "zod"
import { envelope } from "./contracts"

export const apiBase = "/mailbox/api/v1/"
export const pathId = encodeURIComponent
export const projectPath = (project: string) => `projects/${pathId(project)}`

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly retryable: boolean,
    public readonly requestId?: string,
    public readonly loginUrl?: string,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

const errorSchema = z.object({
  error: z.object({ code: z.string(), message: z.string(), retryable: z.boolean(), login_url: z.string().nullish() }).optional(),
  request_id: z.string().optional(),
  login_url: z.string().nullish(),
})

export function request(path: string, signal: AbortSignal, accept = "application/json") {
  return fetch(apiBase + path, {
    signal,
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: accept },
  })
}

export async function responseError(response: Response) {
  const payload = errorSchema.safeParse(await response.json().catch(() => null))
  const body = payload.data
  return new ApiError(
    response.status,
    body?.error?.message || `请求失败（HTTP ${response.status}）`,
    body?.error?.retryable ?? response.status >= 500,
    body?.request_id,
    body?.login_url ?? body?.error?.login_url ?? undefined,
  )
}

export async function get<T extends z.ZodType>(path: string, schema: T, signal: AbortSignal) {
  const response = await request(path, signal)
  if (!response.ok) throw await responseError(response)
  const result = schema.safeParse(await response.json().catch(() => null))
  if (!result.success) {
    throw new ApiError(response.status, "响应格式与监控接口约定不符。", false)
  }
  return result.data
}

export function useSnapshot<T extends z.ZodType>(project: string, path: string, schema: T, enabled = true) {
  return useQuery({
    queryKey: ["project", project, path],
    queryFn: ({ signal }) => get(path, envelope(schema), signal),
    enabled,
  })
}

export function usePages<T extends z.ZodType>(scope: readonly string[], path: string, schema: T) {
  return usePagedSnapshot(scope, path, z.array(schema))
}

export function usePagedSnapshot<T extends z.ZodType>(scope: readonly string[], path: string, schema: T) {
  return useInfiniteQuery({
    queryKey: [...scope, path],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => {
      const query = new URLSearchParams({ limit: "50" })
      if (pageParam) query.set("cursor", pageParam)
      return get(`${path}${path.includes("?") ? "&" : "?"}${query}`, envelope(schema), signal)
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
}

export function scopedPath(path: string, project: string, execution?: string | null) {
  const params = new URLSearchParams({ project_id: project })
  if (execution) params.set("execution_id", execution)
  return `${path}?${params}`
}
