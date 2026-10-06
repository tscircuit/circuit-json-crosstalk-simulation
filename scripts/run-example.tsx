import { renderCoupledRoutes } from "../examples/coupled-routes"
import { exampleSetup } from "../examples/setup"
import { analyzeCrosstalk } from "../lib"

const args = process.argv.slice(2)
const value = (flag: string) => {
  const i = args.indexOf(flag)
  return i < 0 ? undefined : args[i + 1]
}
const mode = args.includes("--native") ? "native" : "export"
const gap = Number(value("--gap") ?? "0.1")
const output = value("--output")
if (!output || !Number.isFinite(gap) || gap <= 0)
  throw new Error(
    "Usage: bun run example --output work/tight --gap 0.1 [--native] [--mesh-near 0.06]",
  )
const circuitJson = await renderCoupledRoutes(gap)
const setup = exampleSetup(circuitJson)
if (value("--mesh-near")) setup.mesh.near_mm = Number(value("--mesh-near"))
if (value("--air-padding"))
  setup.air_padding_mm = Number(value("--air-padding"))
const result = await analyzeCrosstalk(circuitJson, {
  output_directory: output,
  setup,
  mode,
  runtime:
    mode === "native"
      ? {
          python: process.env.PALACE_PYTHON ?? "",
          palace: process.env.PALACE_BIN ?? "",
          seconds: 300,
          memory_bytes: 6 * 1024 ** 3,
          disk_bytes: 8 * 1024 ** 3,
          lock_directory:
            process.env.PALACE_HEAVY_LOCK ?? "/tmp/dot-cloud-heavy.lock",
        }
      : undefined,
})
console.log(JSON.stringify(result, null, 2))
if (!["exported", "native_passed"].includes(result.status)) process.exitCode = 1
