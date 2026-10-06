import { expect, test } from "bun:test"
import { createHash } from "node:crypto"
import { join } from "node:path"

const directory = join(import.meta.dir, "../evidence/2026-10-06")

test("committed native artifacts retain exact hashes", async () => {
  const manifest = await Bun.file(join(directory, "manifest.json")).json()
  for (const [path, expected] of Object.entries(manifest.sha256)) {
    const actual = createHash("sha256")
      .update(await Bun.file(join(directory, path)).bytes())
      .digest("hex")
    expect(actual).toBe(expected as string)
  }
})

test("saved raw native complex S agrees with reported values and passive column powers", async () => {
  for (const name of ["tight", "wide", "refined", "web-ui"]) {
    const summary = await Bun.file(join(directory, name, "summary.json")).json()
    const receipt = await Bun.file(
      join(directory, name, "native-receipt.json"),
    ).json()
    const csv = await Bun.file(
      join(directory, name, "postpro/port-S.csv"),
    ).text()
    const [header, data] = csv
      .trim()
      .split("\n")
      .map((row) => row.split(",").map((v) => v.trim()))
    expect(summary.independent_converged_excitations).toBe(4)
    expect(
      receipt.commands.every((c: { exit_code: number }) => c.exit_code === 0),
    ).toBe(true)
    expect(Number(data[0]) * 1e9).toBe(summary.s_parameters[0].frequency_hz)
    const real = Array.from({ length: 4 }, () => new Array<number>(4).fill(NaN))
    const imag = Array.from({ length: 4 }, () => new Array<number>(4).fill(NaN))
    for (let col = 1; col < header.length; col += 2) {
      const match = header[col].match(/^\|S\[(\d+)\]\[(\d+)\]\| \(dB\)$/)!
      const i = Number(match[1]) - 1,
        j = Number(match[2]) - 1
      const magnitude = 10 ** (Number(data[col]) / 20)
      const phase = (Number(data[col + 1]) * Math.PI) / 180
      real[i][j] = magnitude * Math.cos(phase)
      imag[i][j] = magnitude * Math.sin(phase)
      expect(real[i][j]).toBeCloseTo(summary.s_parameters[0].real[i][j], 12)
      expect(imag[i][j]).toBeCloseTo(summary.s_parameters[0].imag[i][j], 12)
    }
    for (let j = 0; j < 4; j++) {
      const power = real.reduce(
        (sum, row, i) => sum + row[j] ** 2 + imag[i][j] ** 2,
        0,
      )
      expect(power).toBeLessThanOrEqual(1.0001)
    }
  }
})

test("saved failed mesh comparison cannot become a numerical qualification", async () => {
  const comparison = await Bun.file(join(directory, "comparison.json")).json()
  expect(comparison.mesh_comparison.status).toBe("failed")
  expect(
    comparison.mesh_comparison.maximum_relative_selected_coupling_change,
  ).toBeGreaterThan(0.05)
  expect(comparison.truncation_convergence).toBe("not_evaluated")
})
