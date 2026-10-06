import type { PreparedModel } from "./types"

/** Palace driven frequencies use GHz; drawing-unit millimetres use L0=1e-3 m. */
export function palaceConfig(model: PreparedModel) {
  const { setup } = model
  const dielectric = model.stackup.layers[1]
  if (dielectric.type !== "dielectric") throw new Error("Unresolved dielectric")
  return {
    Problem: { Type: "Driven", Verbose: 2, Output: "postpro" },
    Model: { Mesh: "model.msh", L0: model.units.palace_length_scale_m },
    Domains: {
      Materials: [
        { Attributes: [1], Permittivity: 1, Permeability: 1, LossTan: 0 },
        {
          Attributes: [2],
          Permittivity: dielectric.dielectric_constant,
          Permeability: setup.relative_permeability,
          LossTan: dielectric.dielectric_loss_tangent,
        },
      ],
    },
    Boundaries: {
      PEC: { Attributes: [11, 12, 14] },
      Absorbing: { Attributes: [13], Order: 1 },
      LumpedPort: Array.from({ length: 4 }, (_, i) => ({
        Index: i + 1,
        Attributes: [21 + i],
        R: setup.port_resistance_ohms,
        Direction: "-Z",
        Excitation: i + 1,
      })),
    },
    Solver: {
      Order: setup.solver.order,
      Device: "CPU",
      Driven: {
        Samples: [
          { Type: "Point", Freq: setup.frequency_hz.map((f) => f / 1e9) },
        ],
        Save: setup.save_fields_at_hz.map((f) => f / 1e9),
      },
      Linear: {
        Type: setup.solver.type,
        KSPType: "GMRES",
        Tol: setup.solver.tolerance,
        MaxIts: setup.solver.max_iterations,
        ComplexCoarseSolve: true,
        PCMatShifted: false,
      },
    },
  }
}

export function geometrySvg(model: PreparedModel): string {
  const [x0, y0, x1, y1] = model.board.bounds_mm
  const rect = (b: number[], fill: string, opacity = 1) =>
    `<rect x="${b[0]}" y="${-b[3]}" width="${b[2] - b[0]}" height="${b[3] - b[1]}" fill="${fill}" opacity="${opacity}"/>`
  const signals = model.signals
    .map((s) => {
      // Pads cover the round wire caps: the exact union is one rectangle.
      const lo = Math.min(
        s.x_min_mm - s.width_mm / 2,
        ...s.pads.map((p) => p.x_mm - p.width_mm / 2),
      )
      const hi = Math.max(
        s.x_max_mm + s.width_mm / 2,
        ...s.pads.map((p) => p.x_mm + p.width_mm / 2),
      )
      return rect(
        [lo, s.y_mm - s.width_mm / 2, hi, s.y_mm + s.width_mm / 2],
        "#db7d24",
      )
    })
    .join("")
  return `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="440" viewBox="${x0 - 0.2} ${-y1 - 0.2} ${x1 - x0 + 0.4} ${y1 - y0 + 0.4}" role="img" aria-label="Circuit JSON copper geometry; not a simulated field">${rect([x0, y0, x1, y1], "#e1e8e6")}${rect(model.reference.bounds_mm, "#428d9a", 0.45)}${signals}</svg>\n`
}
