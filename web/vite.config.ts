import { fileURLToPath, URL } from "node:url"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig, loadEnv } from "vite"

export default defineConfig(async ({ command, mode, isPreview }) => {
  const fixture = command === "serve" && !isPreview && mode === "fixture"
  if (command === "build" && mode === "fixture") throw new Error("Fixture mode is for development only")
  const upstream = loadEnv(mode, process.cwd(), "MAILBOX_").MAILBOX_API_UPSTREAM
  const proxy = upstream && !fixture ? {
    "/mailbox/api/v1": { target: upstream, changeOrigin: true },
    "/mailbox/auth": { target: upstream, changeOrigin: true },
  } : undefined

  return {
    base: "/mailbox/ui/",
    plugins: [react(), tailwindcss(), ...(fixture ? [(await import("./dev/fixture")).fixturePlugin()] : [])],
    resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
    server: { proxy },
    preview: { proxy },
  }
})
