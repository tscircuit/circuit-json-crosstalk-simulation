import { join } from "node:path"
import { run } from "./lib/run"
import type { AnalysisSetup, CircuitJson } from "./lib/types"

export type { AnalysisResult, AnalysisSetup, CircuitJson } from "./lib/types"

/** Run the real Palace simulation; setup supplies the explicit testbench choices. */
export function simulate(
  circuitJson: CircuitJson,
  options: {
    setup: AnalysisSetup
    output_directory?: string
    signal?: AbortSignal
  },
) {
  return run(circuitJson, {
    setup: options.setup,
    output_directory:
      options.output_directory ?? join("output", crypto.randomUUID()),
    mode: "native",
    signal: options.signal,
    runtime: {
      python: process.env.PALACE_PYTHON ?? Bun.which("python3") ?? "",
      palace: process.env.PALACE_BIN ?? Bun.which("palace") ?? "",
      seconds: 90,
      memory_bytes: 6 * 1024 ** 3,
      disk_bytes: 256 * 1024 ** 2,
      lock_directory:
        process.env.PALACE_HEAVY_LOCK ?? "/tmp/dot-cloud-heavy.lock",
    },
  })
}
