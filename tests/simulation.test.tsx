import { describe, expect, test } from "bun:test"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import type { AnyCircuitElement } from "circuit-json"
import { renderCoupledRoutes } from "../examples/coupled-routes"
import { exampleSetup } from "../examples/setup"
import { eyeConditions, eyeSetup } from "../examples/eye-setup"
import { simulate } from "../index"
import { run } from "../lib/run"
import { prepare } from "../lib/prepare"
import { palaceConfig } from "../lib/config"

const tight = await renderCoupledRoutes(0.1)
const wide = await renderCoupledRoutes(0.8)
const setup = exampleSetup(tight)
const clone = () => structuredClone(tight)
const boardOf = (j = tight) => j.find((e) => e.type === "pcb_board")!
const padOf = (j = tight) =>
  j.find((e) => e.type === "pcb_smtpad" && e.layer === "top")!

describe("rendered inputs and the native simulation API", () => {
  test("two eye layouts change spacing while preserving dimensions, materials and electrical conditions", () => {
    const models = [tight, wide].map((j) => {
      const result = prepare(j, eyeSetup(j))
      if (result.status !== "ready")
        throw new Error("Eye fixture preflight failed")
      return result.model
    })
    expect(models[0].setup.frequency_hz.length).toBe(41)
    expect(models[0].setup.frequency_hz[0]).toBe(1e7)
    expect(models[0].setup.frequency_hz.at(-1)).toBe(1e10)
    expect(models[0].setup.save_fields_at_hz).toEqual([])
    expect(models[0].board).toEqual(models[1].board)
    expect(models[0].stackup).toEqual(models[1].stackup)
    expect(models[0].reference).toEqual(models[1].reference)
    expect(models[0].setup).toEqual(models[1].setup)
    const dimensions = (m: (typeof models)[0]) =>
      m.signals.map((s) => [
        s.x_min_mm,
        s.x_max_mm,
        s.width_mm,
        s.pads.map((p) => [p.width_mm, p.height_mm]),
      ])
    expect(dimensions(models[0])).toEqual(dimensions(models[1]))
    expect(eyeConditions.victim_source_port).toBe(3)
    expect(eyeConditions.aggressor_source_port).toBe(2)
    expect(eyeConditions.victim_receiver_port).toBe(4)
    expect(eyeConditions.source_resistance_ohms).toBe(
      eyeConditions.load_resistance_ohms,
    )
    expect(
      prepare(tight, {
        ...eyeSetup(tight),
        frequency_hz: Array.from({ length: 162 }, (_, i) => i + 1),
      }).status,
    ).toBe("unsupported")
  })
  test("uses the actual smaller reference copper and measured gaps", () => {
    const original = JSON.stringify(tight)
    for (const [j, gap] of [
      [tight, 0.1],
      [wide, 0.8],
    ] as const) {
      const result = prepare(j, exampleSetup(j))
      expect(result.status).toBe("ready")
      if (result.status !== "ready") throw new Error("Fixture preflight failed")
      expect(result.model.reference.bounds_mm).toEqual([-2.8, -1, 2.8, 1])
      expect(result.model.gap_mm).toBeCloseTo(gap, 12)
      expect(result.model.board.thickness_mm).toBe(0.27)
      expect(
        result.model.signals.every((s) => s.x_max_mm - s.x_min_mm === 4),
      ).toBe(true)
    }
    expect(JSON.stringify(tight)).toBe(original)
  })
  test("maps mm -> L0 metres, Hz -> GHz and four independent excitations", async () => {
    const result = prepare(tight, setup)
    if (result.status !== "ready") throw new Error("Fixture preflight failed")
    const config = palaceConfig(result.model)
    expect(config.Model.L0).toBe(0.001)
    expect(config.Solver.Driven.Samples[0].Freq).toEqual([1])
    expect(config.Boundaries.LumpedPort.map((p) => p.Excitation)).toEqual([
      1, 2, 3, 4,
    ])
    expect(config.Domains.Materials[1].Permittivity).toBe(4)
    expect(config.Domains.Materials[1].LossTan).toBe(0)
  })
  test("export-only persists exact inputs and never claims native fields", async () => {
    const root = await mkdtemp(join(tmpdir(), "crosstalk-export-"))
    try {
      const output = join(root, "run")
      const result = await run(tight, {
        output_directory: output,
        setup,
        mode: "export",
      })
      expect(result.status).toBe("exported")
      expect(result.native_status).toBe("never_run")
      expect(result.convergence_status).toBe("not_evaluated")
      expect(await Bun.file(join(output, "circuit.json")).json()).toEqual(tight)
      expect(await Bun.file(join(output, "electric-field.png")).exists()).toBe(
        false,
      )
      expect(await Bun.file(join(output, "model.msh")).exists()).toBe(false)
      await expect(
        run(tight, {
          output_directory: output,
          setup,
          mode: "export",
        }),
      ).rejects.toThrow()
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  })
  test("public simulate runs native mode and cannot report export success without an installed runtime", async () => {
    const root = await mkdtemp(join(tmpdir(), "crosstalk-runtime-"))
    const previous = {
      python: process.env.PALACE_PYTHON,
      palace: process.env.PALACE_BIN,
    }
    try {
      process.env.PALACE_PYTHON = join(root, "missing-python")
      process.env.PALACE_BIN = join(root, "missing-palace")
      const result = await simulate(tight, {
        setup,
        output_directory: join(root, "run"),
      })
      expect(result.status).toBe("runtime_unavailable")
      expect(result.native_status).toBe("never_run")
      expect(await Bun.file(join(root, "run/model.msh")).exists()).toBe(false)
      expect(
        await Bun.file(join(root, "run/electric-field.png")).exists(),
      ).toBe(false)
    } finally {
      if (previous.python === undefined) delete process.env.PALACE_PYTHON
      else process.env.PALACE_PYTHON = previous.python
      if (previous.palace === undefined) delete process.env.PALACE_BIN
      else process.env.PALACE_BIN = previous.palace
      await rm(root, { recursive: true, force: true })
    }
  })
  test("stackup alone cannot produce a simulation", () => {
    expect(prepare(tight).status).toBe("missing_data")
    const j = clone()
    delete boardOf(j).stackup
    expect(prepare(j, setup).status).toBe("missing_data")
  })
  test("unknown loss stays unknown, including when zero would be convenient", () => {
    const j = clone()
    const dielectric = boardOf(j).stackup!.layers[1]
    if (dielectric.type !== "dielectric") throw new Error("Bad fixture")
    delete dielectric.dielectric_loss_tangent
    delete dielectric.dielectric_loss_tangent_frequency_hz
    expect(prepare(j, setup).status).toBe("missing_data")
  })
  test("fill cannot override supplied facts and preserves assumed provenance", () => {
    const j = clone()
    const board = boardOf(j)
    board.stackup!.source = "specified"
    const fill = structuredClone(board.stackup!)
    fill.source = "assumed"
    const dielectric = board.stackup!.layers[1]
    if (dielectric.type !== "dielectric") throw new Error("Bad fixture")
    delete dielectric.dielectric_constant
    delete dielectric.dielectric_constant_frequency_hz
    const result = prepare(j, { ...setup, stackup_fill: fill })
    expect(result.status).toBe("ready")
    if (result.status === "ready")
      expect(result.model.stackup.source).toBe("assumed")
    expect(board.stackup!.source).toBe("specified")
    const override = structuredClone(fill)
    if (override.layers[0].type === "copper")
      override.layers[0].thickness_mm = 0.07
    expect(prepare(j, { ...setup, stackup_fill: override }).status).toBe(
      "unsupported",
    )
    expect(
      prepare(j, {
        ...setup,
        stackup_fill: { ...fill, source: "invalid" as never },
      }).status,
    ).toBe("unsupported")
  })
  test("different dielectric reference frequencies remain explicit", () => {
    const j = clone()
    const d = boardOf(j).stackup!.layers[1]
    if (d.type !== "dielectric") throw new Error("Bad fixture")
    d.dielectric_loss_tangent_frequency_hz = 2e9
    const result = prepare(j, setup)
    expect(result.status).toBe("ready")
    if (result.status === "ready")
      expect(result.model.stackup).toEqual(boardOf(j).stackup!)
  })
  test("rejects holes, vias, stiffeners and reference slots", () => {
    for (const type of ["pcb_via", "pcb_cutout", "pcb_stiffener"]) {
      const j = clone()
      j.push({ type } as AnyCircuitElement)
      expect(prepare(j, setup).status).toBe("unsupported")
    }
    const j = clone()
    const p = j.find((e) => e.type === "pcb_copper_pour")!
    if (p.shape !== "brep") throw new Error("Bad fixture")
    p.brep_shape.inner_rings.push({
      vertices: [
        { x: -0.1, y: -0.1 },
        { x: 0.1, y: -0.1 },
        { x: 0.1, y: 0.1 },
        { x: -0.1, y: 0.1 },
      ],
    })
    expect(prepare(j, setup).status).toBe("unsupported")
  })
  test("rejects bends, tapers, narrower and rounded endpoint pads", () => {
    const bend = clone()
    const t = bend.find((e) => e.type === "pcb_trace")!
    t.route.splice(1, 0, {
      route_type: "wire",
      x: 0,
      y: 0.3,
      width: 0.2,
      layer: "top",
    })
    expect(prepare(bend, setup).status).toBe("unsupported")
    const taper = clone()
    Object.assign(taper.find((e) => e.type === "pcb_trace")!.route[0], {
      start_width: 0.2,
      end_width: 0.1,
    })
    expect(prepare(taper, setup).status).toBe("unsupported")
    const narrow = clone()
    Object.assign(padOf(narrow), { width: 0.1 })
    expect(prepare(narrow, setup).status).toBe("unsupported")
    for (const field of ["corner_radius", "rect_border_radius"]) {
      const j = clone()
      Object.assign(padOf(j), { [field]: 0.05 })
      expect(prepare(j, setup).status).toBe("unsupported")
    }
  })
  test("rejects branched source connectivity and unsupported saved-field counts", () => {
    const j = clone()
    const source = j
      .filter((e) => e.type === "source_trace")
      .find(
        (e) =>
          e.source_trace_id ===
          j.find((e) => e.type === "pcb_trace")!.source_trace_id,
      )!
    source.connected_source_port_ids.push("source_port_1")
    expect(prepare(j, setup).status).toBe("unsupported")
    expect(
      prepare(tight, {
        ...setup,
        frequency_hz: [1e9, 2e9],
        save_fields_at_hz: [1e9, 2e9],
      }).status,
    ).toBe("unsupported")
  })
})
