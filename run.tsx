import { mkdir } from "node:fs/promises"
import { resolve } from "node:path"
import { parseArgs } from "node:util"
import { renderCoupledRoutes } from "./examples/coupled-routes"
import { eyeConditions, eyeSetup } from "./examples/eye-setup"
import { simulate } from "./index"
import { prepare } from "./lib/prepare"

const { values } = parseArgs({
  options: {
    gap: { type: "string" },
    "wide-gap": { type: "string" },
    "mesh-near": { type: "string" },
    output: { type: "string" },
    help: { type: "boolean", short: "h" },
  },
})
if (values.help) {
  console.log(
    "bun run simulate [--gap 0.1] [--wide-gap 0.8] [--mesh-near 0.04] [--output output/my-run]\nRenders two layouts, runs four native broadband channels (two meshes each), then compares quiet/switching victim eyes. Requires installed Python packages and Palace. No cached/synthetic channel fallback.",
  )
  process.exit(0)
}
const gap = Number(values.gap ?? 0.1)
const wideGap = Number(values["wide-gap"] ?? 0.8)
const meshNear = Number(values["mesh-near"] ?? 0.04)
if (
  ![gap, wideGap, meshNear].every((n) => Number.isFinite(n) && n > 0) ||
  wideGap <= gap
)
  throw new Error("Positive gaps/mesh sizes and wide-gap > gap are required")
const controller = new AbortController()
const cancel = () => controller.abort()
process.once("SIGINT", cancel)
process.once("SIGTERM", cancel)
const output = resolve(values.output ?? `output/${crypto.randomUUID()}`)
const python = process.env.PALACE_PYTHON ?? Bun.which("python3") ?? ""
const cases: Record<string, { channel: string; coarse: string }> = {}
const fixtures = []
try {
  await mkdir(resolve(output, ".."), { recursive: true })
  await mkdir(output)
  const postprocess = async (args: string[]) => {
    if (controller.signal.aborted)
      throw new Error("Cancelled before postprocessing")
    const child = Bun.spawn(
      [python, resolve(import.meta.dir, "python/eyes.py"), ...args],
      {
        stdout: "inherit",
        stderr: "inherit",
        env: {
          ...process.env,
          OMP_NUM_THREADS: "1",
          OPENBLAS_NUM_THREADS: "1",
          VECLIB_MAXIMUM_THREADS: "1",
          MPLCONFIGDIR: `${output}/matplotlib-cache`,
        },
      },
    )
    const stop = () => child.kill("SIGTERM")
    controller.signal.addEventListener("abort", stop, { once: true })
    if (controller.signal.aborted) stop()
    const exit = await child.exited
    controller.signal.removeEventListener("abort", stop)
    if (exit !== 0)
      throw new Error(
        `Eye/layout processing failed (${exit}); native outputs preserved in ${output}`,
      )
  }
  // Geometry comes first, from the exact rendered Circuit JSON.
  for (const [name, distance] of [
    ["close", gap],
    ["separated", wideGap],
  ] as const) {
    const circuitJson = await renderCoupledRoutes(distance)
    const setup = eyeSetup(circuitJson, meshNear)
    const preflight = prepare(circuitJson, setup)
    if (preflight.status !== "ready")
      throw new Error(preflight.issues.join("; "))
    const directory = resolve(output, name)
    await mkdir(directory)
    await Bun.write(
      `${directory}/circuit.json`,
      JSON.stringify(circuitJson, null, 2),
    )
    await Bun.write(
      `${directory}/model.json`,
      JSON.stringify(preflight.model, null, 2),
    )
    await postprocess([
      "layout",
      `${directory}/model.json`,
      `${output}/${name}-layout.png`,
      `${name} spacing`,
    ])
    fixtures.push({ name, circuitJson })
    cases[name] = {
      channel: `${directory}/channel`,
      coarse: `${directory}/coarse`,
    }
  }
  console.log(
    `Actual PCB layouts saved first: ${output}/close-layout.png and separated-layout.png`,
  )
  await Bun.write(
    `${output}/eye-input.json`,
    JSON.stringify({ conditions: eyeConditions, cases }, null, 2),
  )
  for (const { name, circuitJson } of fixtures) {
    for (const [kind, near] of [
      ["coarse", meshNear * 1.5],
      ["channel", meshNear],
    ] as const) {
      console.log(
        `${name}: native broadband ${kind}, near mesh ${near} mm, 41 frequencies × 4 excitations`,
      )
      const result = await simulate(circuitJson, {
        setup: eyeSetup(circuitJson, near),
        output_directory: cases[name][kind],
        signal: controller.signal,
      })
      console.log(JSON.stringify(result, null, 2))
      if (result.status !== "native_passed")
        throw new Error(`${name}/${kind} failed; inspect saved result/logs`)
    }
  }
  await postprocess([output])
  const summary = await Bun.file(`${output}/eye-summary.json`).json()
  console.log(
    `Open ${output}/index.html. Layouts first, then same-axis eyes and added noise. ${summary.status}.`,
  )
  if (summary.status !== "loaded_voltage_checks_passed") process.exitCode = 1
} finally {
  process.removeListener("SIGINT", cancel)
  process.removeListener("SIGTERM", cancel)
}
