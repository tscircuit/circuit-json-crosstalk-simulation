import { exampleSetup } from "./setup"
import type { CircuitJson } from "../lib/types"

/** The extra 10 MHz solve checks the separately stated ideal-PEC DC limit. */
export function eyeSetup(circuitJson: CircuitJson, near_mm = 0.04) {
  const setup = exampleSetup(circuitJson)
  setup.frequency_hz = [
    1e7,
    ...Array.from({ length: 40 }, (_, i) => (i + 1) * 2.5e8),
  ]
  setup.save_fields_at_hz = [1e9]
  setup.mesh = { near_mm, far_mm: 0.45, transition_mm: 0.4 }
  return setup
}

/** Educational linear sources and matched loads, not DDR/IBIS device models. */
export const eyeConditions = {
  bit_rate_hz: 1.6e9,
  driver_low_v: 0,
  driver_high_v: 1.5,
  rise_fall_s: 2e-10,
  source_resistance_ohms: 50,
  load_resistance_ohms: 50,
  victim_source_port: 3,
  aggressor_source_port: 2,
  victim_receiver_port: 4,
  aggressor_phase_ui: 0.5,
  quiet_aggressor_voltage_v: 0,
  bits: 128,
  victim_seed: 871,
  aggressor_seed: 40,
  discard_bits: 8,
  end_margin_bits: 4,
  fixed_eye_delay_s: 2.5e-11,
  threshold_v: 0.375,
  time_step_s: 2.5e-12,
  waveform_tolerance_v: 0.005,
  eye_opening_tolerance_v: 0.005,
  driver_model: "Ideal linear ramp voltage source with series resistance",
  receiver_model:
    "Matched resistor to ideal GND; no receiver capacitance or clamps",
  omitted: "IBIS, package, PDN, jitter, noise, equalization and DDR compliance",
}
