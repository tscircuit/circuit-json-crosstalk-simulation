import { resolve } from "node:path"
import { stat } from "node:fs/promises"

export async function renderRuntime() {
  const python = process.env.PALACE_PYTHON ?? Bun.which("python3")
  const pvpython = process.env.PARAVIEW_PYTHON ?? Bun.which("pvpython")
  if (!python || !pvpython)
    throw new Error(
      "Rendering requires installed Python and ParaView pvpython (PARAVIEW_PYTHON)",
    )
  for (const path of [python, pvpython])
    if (!(await stat(path)).isFile())
      throw new Error(`Runtime is not a file: ${path}`)
  return { python, pvpython }
}

/** Render saved data only. Never launches a solver or fabricates missing fields. */
export async function render(output: string) {
  output = resolve(output)
  const { python, pvpython } = await renderRuntime()
  await Bun.write(
    `${output}/render-runtime.json`,
    JSON.stringify(
      {
        python,
        pvpython,
        seconds: 120,
        memory_bytes: 6 * 1024 ** 3,
        disk_bytes: 8 * 1024 ** 3,
        lock_directory:
          process.env.PALACE_HEAVY_LOCK ?? "/tmp/dot-cloud-heavy.lock",
      },
      null,
      2,
    ) + "\n",
  )
  const child = Bun.spawn(
    [
      python,
      resolve(import.meta.dir, "python/run_native.py"),
      output,
      "--render",
    ],
    {
      stdout: "inherit",
      stderr: "inherit",
      env: {
        ...process.env,
        OMP_NUM_THREADS: "1",
        OPENBLAS_NUM_THREADS: "1",
        VECLIB_MAXIMUM_THREADS: "1",
      },
    },
  )
  const stop = () => child.kill("SIGTERM")
  process.once("SIGINT", stop)
  process.once("SIGTERM", stop)
  try {
    if ((await child.exited) !== 0)
      throw new Error(
        `Saved-data render failed; see ${output}/render-receipt.json`,
      )
  } finally {
    process.removeListener("SIGINT", stop)
    process.removeListener("SIGTERM", stop)
  }
}

if (import.meta.main) await render(Bun.argv[2])
