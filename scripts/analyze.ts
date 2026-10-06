import { parseArgs } from "node:util"
import { analyzeCrosstalk, type AnalysisSetup, type CircuitJson } from "../lib"

const { values } = parseArgs({
  args: process.argv.slice(2),
  options: {
    circuit: { type: "string" },
    setup: { type: "string" },
    output: { type: "string" },
    native: { type: "boolean" },
  },
})
if (!values.circuit || !values.setup || !values.output)
  throw new Error(
    "Usage: bun run analyze --circuit circuit.json --setup setup.json --output work/run [--native]",
  )
const circuitJson = (await Bun.file(values.circuit).json()) as CircuitJson
if (!Array.isArray(circuitJson))
  throw new Error("Circuit JSON must be an array")
const setup = (await Bun.file(values.setup).json()) as AnalysisSetup
const result = await analyzeCrosstalk(circuitJson, {
  output_directory: values.output,
  setup,
  mode: values.native ? "native" : "export",
  runtime: values.native
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
