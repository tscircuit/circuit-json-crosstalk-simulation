import type { AnalysisSetup, CircuitJson } from "../lib/types"

/** Explicit testbench for the synthetic example; separate from board data. */
export function exampleSetup(circuitJson: CircuitJson): AnalysisSetup {
  const traces = circuitJson.filter((e) => e.type === "pcb_trace")
  const pours = circuitJson.filter((e) => e.type === "pcb_copper_pour")
  if (traces.length !== 2 || pours.length !== 1 || !pours[0].source_net_id)
    throw new Error("Unexpected fixture topology")
  return {
    trace_ids: traces.map((t) => t.pcb_trace_id) as [string, string],
    reference_copper_id: pours[0].pcb_copper_pour_id,
    reference_net_id: pours[0].source_net_id,
    conductor_model: "pec",
    dielectric_model: "constant_er_and_loss_tangent",
    relative_permeability: 1,
    soldermask: "omitted",
    port_inset_mm: 0.4,
    port_resistance_ohms: 50,
    frequency_hz: [1e9],
    save_fields_at_hz: [1e9],
    air_padding_mm: 1,
    mesh: { near_mm: 0.08, far_mm: 0.6, transition_mm: 0.4 },
    boundary: "first_order_absorbing",
    solver: { order: 1, type: "SuperLU", tolerance: 1e-9, max_iterations: 200 },
    checks: {
      passivity_tolerance: 1e-4,
      reciprocity_tolerance: 1e-4,
    },
  }
}
