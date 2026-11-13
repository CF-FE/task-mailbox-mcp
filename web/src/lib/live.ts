import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { EventSourceParserStream } from "eventsource-parser/stream"
import { ApiError, projectPath, request, responseError } from "./api"
import { cursorSchema } from "./contracts"

type Connection = "connecting" | "live" | "offline"

export function useLiveUpdates(project: string, watermark: string | undefined, onAuthFailure: (error: ApiError) => void) {
  const client = useQueryClient()
  const [connection, setConnection] = useState<Connection>("connecting")

  // Use the first successful snapshot to establish the stream. Reconnect from
  // the last event already seen, while independently refreshing query snapshots.
  const ready = watermark !== undefined

  useEffect(() => {
    if (!ready) return

    const controller = new AbortController()
    const cached = client.getQueryData<{ pages: { watermark: string }[] }>(["project", project, projectPath(project) + "/overview"])
    let after = cached?.pages[0]?.watermark ?? "0"
    let reconnect: number | undefined
    let refreshTimer: number | undefined
    let failures = 0
    let firstConnection = true
    let refreshing = false
    let dirty = false

    function refresh() {
      if (controller.signal.aborted) return
      dirty = true
      if (refreshing || refreshTimer !== undefined) return
      refreshTimer = window.setTimeout(async () => {
        refreshTimer = undefined
        dirty = false
        refreshing = true
        try {
          await client.invalidateQueries({ queryKey: ["project", project] })
        } finally {
          refreshing = false
          if (dirty) refresh()
        }
      }, 100)
    }

    async function connect() {
      const socket = new AbortController()
      const abortSocket = () => socket.abort()
      let idleTimer: number | undefined
      const connectTimer = window.setTimeout(abortSocket, 20_000)
      controller.signal.addEventListener("abort", abortSocket, { once: true })
      const armIdle = () => {
        window.clearTimeout(idleTimer)
        idleTimer = window.setTimeout(abortSocket, 45_000)
      }
      try {
        const query = new URLSearchParams({ after })
        const response = await request(`${projectPath(project)}/events?${query}`, socket.signal, "text/event-stream")
        if (!response.ok) throw await responseError(response)
        if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) {
          await response.body?.cancel()
          throw new Error("实时流响应无效")
        }

        window.clearTimeout(connectTimer)
        armIdle()
        setConnection("live")
        // Covers a disconnect between receiving an event and reading its data.
        if (!firstConnection) refresh()
        firstConnection = false
        const reader = response.body
          .pipeThrough(new TextDecoderStream())
          .pipeThrough(new TransformStream<string, string>({
            transform(chunk, output) {
              armIdle()
              output.enqueue(chunk)
            },
          }))
          .pipeThrough(new EventSourceParserStream())
          .getReader()
        try {
          while (!controller.signal.aborted) {
            const { done, value } = await reader.read()
            if (done) break
            if (value.event === "heartbeat" || value.event === "ping") continue
            let cursor: unknown = value.id
            if (!cursor) {
              try {
                cursor = JSON.parse(value.data)?.watermark
              } catch {
                // A hint without a cursor can still trigger a snapshot refresh.
              }
            }
            const parsed = cursorSchema.safeParse(cursor)
            if (parsed.success) after = String(parsed.data)
            failures = 0
            refresh()
          }
        } finally {
          reader.releaseLock()
        }
      } catch (error) {
        if (controller.signal.aborted) return
        if (error instanceof ApiError && error.status === 401) {
          onAuthFailure(error)
          return
        }
        if (error instanceof ApiError && error.status === 403) {
          void client.invalidateQueries({ queryKey: ["session"] })
        }
      } finally {
        window.clearTimeout(connectTimer)
        controller.signal.removeEventListener("abort", abortSocket)
        window.clearTimeout(idleTimer)
        socket.abort()
      }
      if (controller.signal.aborted) return
      setConnection("offline")
      refresh()
      reconnect = window.setTimeout(() => void connect(), Math.min(1000 * 2 ** failures, 15000))
      failures = Math.min(failures + 1, 4)
    }

    // Freshness ages even when the source has no new events.
    const freshnessTimer = window.setInterval(refresh, 30_000)
    setConnection("connecting")
    void connect()
    return () => {
      controller.abort()
      window.clearTimeout(reconnect)
      window.clearTimeout(refreshTimer)
      window.clearInterval(freshnessTimer)
    }
    // A newer snapshot does not reset the live stream's own cursor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, project, ready, onAuthFailure])

  return ready ? connection : "connecting"
}
