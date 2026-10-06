import { parseArgs } from "node:util"
import { renderCoupledRoutes } from "./examples/coupled-routes"
import { exampleSetup } from "./examples/setup"
import { simulate } from "./index"

const { values } = parseArgs({
  options: {
    gap: { type: "string" },
    "mesh-near": { type: "string" },
    output: { type: "string" },
    help: { type: "boolean", short: "h" },
  },
})
if (values.help) {
  console.log(
    "bun run simulate [--gap 0.1] [--mesh-near 0.08] [--output output/my-run]\nRequires Bun, Palace and Python packages in python/requirements.txt. Always runs the native solver.",
  )
  process.exit(0)
}
const gap = Number(values.gap ?? 0.1)
if (!Number.isFinite(gap) || gap <= 0)
  throw new Error("--gap must be a positive distance in mm")
const controller = new AbortController()
const cancel = () => controller.abort()
process.once("SIGINT", cancel)
process.once("SIGTERM", cancel)
try {
  const circuitJson = await renderCoupledRoutes(gap)
  const setup = exampleSetup(circuitJson)
  if (values["mesh-near"]) setup.mesh.near_mm = Number(values["mesh-near"])
  const result = await simulate(circuitJson, {
    setup,
    output_directory: values.output,
    signal: controller.signal,
  })
  console.log(JSON.stringify(result, null, 2))
  if (result.status !== "native_passed") process.exitCode = 1
} finally {
  process.removeListener("SIGINT", cancel)
  process.removeListener("SIGTERM", cancel)
}
