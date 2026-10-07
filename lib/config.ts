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
