import { expect, test } from "bun:test"
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { startServer } from "../server"
import type { AnalysisResult } from "../index"
import type { Execute } from "../server/jobs"

const complete: AnalysisResult = {
  status: "native_passed",
  issues: [],
  native_status: "passed",
  convergence_status: "not_evaluated",
}
async function withServer(
  execute: Execute,
  check: (
    url: string,
    post: (path: string, body: unknown) => Promise<Response>,
  ) => Promise<void>,
) {
  const root = await mkdtemp(join(tmpdir(), "palace-ui-test-"))
  const app = await startServer({ root, port: 0, execute })
  const html = await (await fetch(app.url)).text()
  const token = JSON.parse(html.match(/const csrfToken\s*=\s*("[^"]+")/)![1])
  const post = (path: string, body: unknown) =>
    fetch(app.url + path, {
      method: "POST",
      headers: {
        Origin: app.url,
        "Content-Type": "application/json",
        "X-Simulation-Token": token,
      },
      body: JSON.stringify(body),
    })
  try {
    await check(app.url, post)
  } finally {
    await app.stop()
    await rm(root, { recursive: true, force: true })
  }
}
async function waitFor(url: string, id: string, state: string) {
  for (let i = 0; i < 100; i++) {
    const job = await (await fetch(`${url}/api/jobs/${id}`)).json()
    if (job.state === state) return job
    await Bun.sleep(5)
  }
  throw new Error(`Job did not reach ${state}`)
}

test("loopback API reserves before render, rejects busy and permits a fresh run after completion", async () => {
  let finish: ((value: AnalysisResult) => void) | undefined
  const execute: Execute = (_fixture, _output, signal) =>
    new Promise((resolve) => {
      finish = resolve
      if (signal.aborted) {
        resolve({
          ...complete,
          status: "cancelled",
          native_status: "never_run",
        })
        return
      }
      signal.addEventListener(
        "abort",
        () =>
          resolve({
            ...complete,
            status: "cancelled",
            native_status: "never_run",
          }),
        { once: true },
      )
    })
  await withServer(execute, async (url, post) => {
    const response = await post("/api/jobs", { fixture: "tight" })
    expect(response.status).toBe(202)
    const job = await response.json()
    expect((await post("/api/jobs", { fixture: "wide" })).status).toBe(409)
    while (!finish) await Bun.sleep(1)
    finish(complete)
    const done = await waitFor(url, job.id, "completed")
    expect(done.convergence).toEqual({
      mesh: "not_evaluated",
      truncation: "not_evaluated",
    })
    expect(done.artifacts).toEqual([]) // Lifecycle mock does not create solver evidence.
    const next = await post("/api/jobs", { fixture: "wide" })
    expect(next.status).toBe(202)
    const nextJob = await next.json()
    expect(nextJob.id).not.toBe(job.id)
    while (!finish) await Bun.sleep(1)
    expect((await post(`/api/jobs/${nextJob.id}/cancel`, {})).status).toBe(202)
    await waitFor(url, nextJob.id, "cancelled")
  })
})
test("request validation blocks foreign Host/Origin, missing token, arbitrary code/paths/settings", async () => {
  await withServer(
    async () => complete,
    async (url, post) => {
      expect(
        (await fetch(url, { headers: { Host: "attacker.invalid" } })).status,
      ).toBe(403)
      expect(
        (await fetch(url, { headers: { Origin: "http://attacker.invalid" } }))
          .status,
      ).toBe(403)
      expect(
        (
          await fetch(url + "/api/jobs", {
            method: "POST",
            headers: { Origin: url, "Content-Type": "application/json" },
            body: '{"fixture":"tight"}',
          })
        ).status,
      ).toBe(403)
      for (const body of [
        { fixture: "__proto__" },
        { fixture: "constructor" },
        { fixture: "arbitrary" },
        { fixture: "tight", command: "echo unsafe" },
        { fixture: "tight", output: "/tmp/other-project" },
        { fixture: "tight", mesh: 0.001 },
        [],
      ])
        expect((await post("/api/jobs", body)).status).toBe(400)
      expect(
        (await post("/api/jobs", { fixture: "x".repeat(300) })).status,
      ).toBe(413)
      expect(
        (
          await fetch(
            url +
              "/api/jobs/00000000-0000-0000-0000-000000000000/artifacts/private.txt",
          )
        ).status,
      ).toBe(404)
    },
  )
})
test("executor errors become truthful failed jobs and release the busy slot", async () => {
  await withServer(
    async () => {
      throw new Error("Deliberate isolated lifecycle failure")
    },
    async (url, post) => {
      const job = await (await post("/api/jobs", { fixture: "tight" })).json()
      const failed = await waitFor(url, job.id, "failed")
      expect(failed.issue).toBe("Deliberate isolated lifecycle failure")
      expect(failed.artifacts).toEqual([])
      expect((await post(`/api/jobs/${job.id}/cancel`, {})).status).toBe(409)
      expect((await post("/api/jobs", { fixture: "wide" })).status).toBe(202)
    },
  )
})

test("owned regular artifacts are served, symlinks are rejected", async () => {
  await withServer(
    async (_fixture, output) => {
      await mkdir(output, { recursive: true })
      await writeFile(
        join(output, "summary.json"),
        '{"isolated_artifact_test":true}',
      )
      const outside = join(output, "../../outside.marker")
      await writeFile(outside, "harmless isolated probe")
      await symlink(outside, join(output, "geometry.svg"))
      return complete
    },
    async (url, post) => {
      const job = await (await post("/api/jobs", { fixture: "tight" })).json()
      await waitFor(url, job.id, "completed")
      expect(
        (await fetch(`${url}/api/jobs/${job.id}/artifacts/summary.json`))
          .status,
      ).toBe(200)
      expect(
        (await fetch(`${url}/api/jobs/${job.id}/artifacts/geometry.svg`))
          .status,
      ).toBe(403)
    },
  )
})
