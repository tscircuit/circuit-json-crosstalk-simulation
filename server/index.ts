import { lstat, mkdir, realpath } from "node:fs/promises"
import { join, resolve } from "node:path"
import { artifacts, fixtures, Jobs, nativeExecutor, type Execute } from "./jobs"
import type { Runtime } from "../lib"

export async function startServer(options: {
  root: string
  port: number
  execute: Execute
}) {
  await mkdir(options.root, { recursive: true })
  const jobs = new Jobs(await realpath(options.root), options.execute)
  const token = crypto.randomUUID()
  const nonce = crypto.randomUUID()
  const html = (await Bun.file(join(import.meta.dir, "ui.html")).text())
    .replaceAll("%%NONCE%%", nonce)
    .replace("%%TOKEN%%", JSON.stringify(token))
  let port = options.port
  const headers = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": `default-src 'self'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'`,
  }
  const json = (data: unknown, status = 200) =>
    Response.json(data, { status, headers })
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port,
    maxRequestBodySize: 256,
    async fetch(request) {
      const host = request.headers.get("host")
      const allowedHosts = [`127.0.0.1:${port}`, `localhost:${port}`]
      if (!host || !allowedHosts.includes(host))
        return json({ error: "Host is not allowed" }, 403)
      const origin = request.headers.get("origin")
      if (origin && !allowedHosts.some((h) => origin === `http://${h}`))
        return json({ error: "Origin is not allowed" }, 403)
      const url = new URL(request.url)
      if (request.method === "POST") {
        if (
          !origin ||
          request.headers.get("x-simulation-token") !== token ||
          request.headers.get("content-type") !== "application/json"
        )
          return json(
            { error: "Same-origin JSON request and simulation token required" },
            403,
          )
      } else if (request.method !== "GET")
        return json({ error: "Method is not allowed" }, 405)
      try {
        if (request.method === "GET" && url.pathname === "/")
          return new Response(html, {
            headers: { ...headers, "Content-Type": "text/html; charset=utf-8" },
          })
        if (request.method === "GET" && url.pathname === "/api/jobs")
          return json({
            fixtures,
            latest: jobs.latest,
            jobs: [...jobs.jobs.values()],
          })
        if (request.method === "POST" && url.pathname === "/api/jobs") {
          const body = await request.text()
          if (body.length > 256)
            return json({ error: "Request is too large" }, 413)
          const data = JSON.parse(body)
          if (
            !data ||
            Array.isArray(data) ||
            Object.keys(data).length !== 1 ||
            typeof data.fixture !== "string" ||
            !Object.hasOwn(fixtures, data.fixture)
          )
            return json(
              {
                error:
                  "Select one of the fixed fixtures; no other settings are accepted",
              },
              400,
            )
          const job = jobs.start(data.fixture)
          return json(job, 202)
        }
        const match = url.pathname.match(
          /^\/api\/jobs\/([a-f0-9-]{36})(?:\/(cancel|artifacts\/([a-zA-Z0-9.-]+)))?$/,
        )
        if (!match || !jobs.jobs.has(match[1]))
          return json({ error: "Not found" }, 404)
        const id = match[1]
        if (request.method === "POST" && match[2] === "cancel") {
          if ((await request.text()) !== "{}")
            return json({ error: "Cancellation body must be {}" }, 400)
          return jobs.cancel(id)
            ? json({ state: "cancelling" }, 202)
            : json({ error: "Job is no longer running" }, 409)
        }
        if (request.method === "GET" && !match[2])
          return json(await jobs.details(id))
        if (request.method === "GET" && match[3] && artifacts.has(match[3])) {
          const path = join(jobs.output(id), match[3])
          const file = Bun.file(path)
          if (await file.exists()) {
            const info = await lstat(path)
            if (
              info.isSymbolicLink() ||
              !info.isFile() ||
              (await realpath(path)) !== path
            )
              return json(
                { error: "Only owned regular artifacts are served" },
                403,
              )
          }
          return (await file.exists())
            ? new Response(file, { headers })
            : json({ error: "Artifact not available" }, 404)
        }
        return json({ error: "Not found" }, 404)
      } catch (error) {
        return json(
          { error: error instanceof Error ? error.message : String(error) },
          error instanceof Error && error.message === "busy" ? 409 : 400,
        )
      }
    },
  })
  port = server.port!
  return {
    server,
    jobs,
    url: `http://127.0.0.1:${port}`,
    stop: async () => {
      await jobs.shutdown()
      server.stop(true)
    },
  }
}

if (import.meta.main) {
  const port = Number(process.env.PALACE_WEB_PORT ?? 3217)
  if (!Number.isInteger(port) || port < 1024 || port > 65535)
    throw new Error("PALACE_WEB_PORT must be 1024–65535")
  const runtime: Runtime = {
    python: process.env.PALACE_PYTHON ?? "",
    palace: process.env.PALACE_BIN ?? "",
    seconds: 90,
    memory_bytes: 6 * 1024 ** 3,
    disk_bytes: 256 * 1024 ** 2,
    lock_directory:
      process.env.PALACE_HEAVY_LOCK ?? "/tmp/dot-cloud-heavy.lock",
  }
  const app = await startServer({
    root: resolve(import.meta.dir, "../work/web-jobs"),
    port,
    execute: nativeExecutor(runtime),
  })
  console.log(
    `Palace local Run UI: ${app.url}\nFixed TSX fixtures only; real native jobs, one at a time. Ctrl-C stops owned work safely.`,
  )
  const stop = async () => {
    await app.stop()
    process.exit(0)
  }
  process.once("SIGINT", stop)
  process.once("SIGTERM", stop)
}
