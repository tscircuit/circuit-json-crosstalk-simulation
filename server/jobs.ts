import { mkdir, stat } from "node:fs/promises"
import { join } from "node:path"
import { renderCoupledRoutes } from "../examples/coupled-routes"
import { exampleSetup } from "../examples/setup"
import { simulate, type AnalysisResult } from "../index"

export const fixtures = {
  tight: { gap: 0.1, mesh: 0.08, label: "Tight: 0.1 mm gap" },
  wide: { gap: 0.8, mesh: 0.08, label: "Wide: 0.8 mm gap" },
  refined: { gap: 0.1, mesh: 0.06, label: "Refined tight: 0.06 mm near mesh" },
} as const
export type Fixture = keyof typeof fixtures
type State = "running" | "cancelling" | "completed" | "failed" | "cancelled"
export interface Job {
  id: string
  fixture: Fixture
  state: State
  started_at: string
  finished_at?: string
  result?: AnalysisResult
  issue?: string
}
export type Execute = (
  fixture: Fixture,
  output: string,
  signal: AbortSignal,
) => Promise<AnalysisResult>
export const artifacts = new Set([
  "geometry.svg",
  "s-matrix.png",
  "electric-field.png",
  "summary.json",
  "circuit.json",
  "setup.json",
  "palace.json",
  "native-receipt.json",
])

/** Reserve synchronously before rendering; cancellation reaches the Python supervisor. */
export class Jobs {
  readonly jobs = new Map<string, Job>()
  private active?: {
    job: Job
    controller: AbortController
    settled: Promise<void>
  }
  latest?: string
  constructor(
    readonly root: string,
    private execute: Execute,
  ) {}
  start(fixture: Fixture): Job {
    if (!Object.hasOwn(fixtures, fixture))
      throw new Error("Unsupported fixture")
    if (this.active) throw new Error("busy")
    if (this.jobs.size >= 16)
      throw new Error(
        "Session job limit reached; restart server to begin a new session",
      )
    const job: Job = {
      id: crypto.randomUUID(),
      fixture,
      state: "running",
      started_at: new Date().toISOString(),
    }
    const controller = new AbortController()
    this.jobs.set(job.id, job)
    this.latest = job.id
    const active = { job, controller, settled: Promise.resolve() }
    this.active = active
    const deadline = setTimeout(() => {
      job.issue = "UI job wall limit reached"
      job.state = "cancelling"
      controller.abort()
    }, 90_000)
    active.settled = Promise.resolve().then(async () => {
      try {
        await mkdir(join(this.root, job.id), { recursive: false })
        job.result = await this.execute(
          fixture,
          this.output(job.id),
          controller.signal,
        )
        job.state =
          job.result.status === "cancelled"
            ? "cancelled"
            : job.result.status === "native_passed"
              ? "completed"
              : "failed"
      } catch (error) {
        job.state = controller.signal.aborted ? "cancelled" : "failed"
        job.issue = error instanceof Error ? error.message : String(error)
      } finally {
        clearTimeout(deadline)
        job.finished_at = new Date().toISOString()
        if (this.active === active) this.active = undefined
        await Bun.write(
          join(this.root, job.id, "web-job.json"),
          JSON.stringify(job, null, 2) + "\n",
        )
      }
    })
    return job
  }
  output(id: string) {
    return join(this.root, id, "run")
  }
  cancel(id: string): boolean {
    if (this.active?.job.id !== id) return false
    this.active.job.state = "cancelling"
    this.active.controller.abort()
    return true
  }
  async shutdown() {
    if (this.active) {
      const active = this.active
      this.cancel(active.job.id)
      await active.settled
    }
  }
  async details(id: string) {
    const job = this.jobs.get(id)
    if (!job) return null
    const output = this.output(id)
    const names = ["runner", "runner-error", "mesh", "palace", "results"]
    const logs: string[] = []
    let phase = "rendering / preparing"
    for (const name of names) {
      const file = Bun.file(join(output, `${name}.log`))
      if (await file.exists()) {
        if (name === "mesh") phase = "meshing"
        if (name === "palace") phase = "native Palace solve"
        if (name === "results") phase = "postprocessing native outputs"
        const size = (await stat(join(output, `${name}.log`))).size
        logs.push(
          `--- ${name}.log (last 12 KiB) ---\n${await file.slice(Math.max(0, size - 12288)).text()}`,
        )
      }
    }
    const available = []
    for (const name of artifacts)
      if (await Bun.file(join(output, name)).exists()) available.push(name)
    return {
      ...job,
      phase:
        job.state === "running" || job.state === "cancelling"
          ? phase
          : job.state,
      logs: logs.join("\n"),
      artifacts: available,
      convergence: { mesh: "not_evaluated", truncation: "not_evaluated" },
    }
  }
}

export const nativeExecutor: Execute = async (fixture, output, signal) => {
  const selected = fixtures[fixture]
  const circuitJson = await renderCoupledRoutes(selected.gap)
  const setup = exampleSetup(circuitJson)
  setup.mesh.near_mm = selected.mesh
  return simulate(circuitJson, {
    output_directory: output,
    setup,
    signal,
  })
}
