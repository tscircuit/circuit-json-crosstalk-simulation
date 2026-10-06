import { mkdir, stat } from "node:fs/promises"
import { dirname, resolve } from "node:path"
import { createHash } from "node:crypto"
import { prepare } from "./prepare"
import { geometrySvg, palaceConfig } from "./config"
import type { AnalyzeOptions, AnalysisResult, CircuitJson } from "./types"
export { prepare } from "./prepare"
export type * from "./types"

const json = (value: unknown) => JSON.stringify(value, null, 2) + "\n"

/** Bun API. Export is deterministic and never represents a completed solver run. */
export async function analyzeCrosstalk(
  circuitJson: CircuitJson,
  options: AnalyzeOptions,
): Promise<AnalysisResult> {
  const preflight = prepare(circuitJson, options.setup)
  if (preflight.status !== "ready")
    return {
      ...preflight,
      native_status: "never_run",
      convergence_status: "not_evaluated",
    }
  if (!["export", "native"].includes(options.mode))
    throw new Error("Select export or native mode explicitly")
  if (!options.output_directory)
    throw new Error("An output_directory is required")
  const output = resolve(options.output_directory)
  await mkdir(dirname(output), { recursive: true })
  await mkdir(output) // Exclusive creation: never overwrite another run or project.
  const write = (name: string, value: unknown) =>
    Bun.write(`${output}/${name}`, json(value))
  const input = json(circuitJson)
  await Bun.write(`${output}/circuit.json`, input)
  await write("setup.json", options.setup)
  await write("model.json", preflight.model)
  await write("palace.json", palaceConfig(preflight.model))
  await Bun.write(`${output}/geometry.svg`, geometrySvg(preflight.model))
  const result: AnalysisResult = {
    status: "exported",
    output_directory: output,
    issues: [],
    native_status: "never_run",
    convergence_status: "not_evaluated",
  }
  const provenance = {
    circuit_sha256: createHash("sha256").update(input).digest("hex"),
    adapter: "straight_pair_palace",
    notices: preflight.model.notices,
    native_status: "never_run",
    mesh_convergence: "not_evaluated",
    truncation_convergence: "not_evaluated",
  }
  await write("provenance.json", provenance)
  if (options.mode === "export") {
    await write("result.json", result)
    return result
  }
  const runtime = options.runtime
  if (!runtime || !runtime.python || !runtime.palace) {
    result.status = "runtime_unavailable"
    result.issues = [
      "Native mode requires explicit Python and Palace runtime paths",
    ]
  } else {
    try {
      for (const path of [runtime.python, runtime.palace])
        if (!(await stat(path)).isFile())
          throw new Error("Runtime is not a file")
      if (
        ![runtime.seconds, runtime.memory_bytes, runtime.disk_bytes].every(
          (n) => Number.isFinite(n) && n > 0,
        ) ||
        runtime.seconds > 600 ||
        runtime.memory_bytes > 6 * 1024 ** 3 ||
        runtime.disk_bytes > 8 * 1024 ** 3 ||
        !runtime.lock_directory
      )
        throw new Error(
          "Native runtime requires explicit bounded wall (<=600s), memory (<=6GiB), disk (<=8GiB) and shared lock",
        )
      await write("runtime.json", runtime)
      const child = Bun.spawn(
        [
          runtime.python,
          resolve(import.meta.dir, "../python/run_native.py"),
          output,
        ],
        {
          stdout: Bun.file(`${output}/runner.log`),
          stderr: Bun.file(`${output}/runner-error.log`),
          env: {
            ...process.env,
            OMP_NUM_THREADS: "1",
            OPENBLAS_NUM_THREADS: "1",
            VECLIB_MAXIMUM_THREADS: "1",
          },
        },
      )
      const exit = await child.exited
      const receiptPath = `${output}/native-receipt.json`
      const receipt = (await Bun.file(receiptPath).exists())
        ? await Bun.file(receiptPath).json()
        : null
      result.native_status = receipt?.native_status ?? "never_run"
      result.status =
        exit === 0 && result.native_status === "passed"
          ? "native_passed"
          : "native_failed"
      result.issues = receipt?.issues ?? [`Native runner exited ${exit}`]
    } catch (error) {
      result.status = "runtime_unavailable"
      result.issues = [error instanceof Error ? error.message : String(error)]
    }
  }
  await write("provenance.json", {
    ...provenance,
    native_status: result.native_status,
  })
  await write("result.json", result)
  return result
}
