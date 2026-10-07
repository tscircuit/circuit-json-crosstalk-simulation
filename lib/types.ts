import type { AnyCircuitElement, PcbBoard } from "circuit-json"

export type CircuitJson = readonly AnyCircuitElement[]
export type Stackup = NonNullable<PcbBoard["stackup"]>

/** Testbench/model choices are separate from manufacturer-independent board data. */
export interface AnalysisSetup {
  trace_ids: [string, string]
  reference_copper_id: string
  reference_net_id: string
  /** Fill unknown stackup fields only; known Circuit JSON values cannot be overridden. */
  stackup_fill?: Stackup
  conductor_model: "pec"
  dielectric_model: "constant_er_and_loss_tangent"
  relative_permeability: 1
  soldermask: "omitted"
  port_inset_mm: number
  port_resistance_ohms: number
  frequency_hz: number[]
  save_fields_at_hz: number[]
  air_padding_mm: number
  mesh: { near_mm: number; far_mm: number; transition_mm: number }
  boundary: "first_order_absorbing"
  solver: {
    order: 1
    type: "SuperLU"
    tolerance: number
    max_iterations: number
  }
  checks: {
    passivity_tolerance: number
    reciprocity_tolerance: number
  }
}

export interface PreparedModel {
  units: { geometry: "mm"; frequency: "Hz"; palace_length_scale_m: 0.001 }
  board: {
    id: string
    bounds_mm: [number, number, number, number]
    thickness_mm: number
  }
  stackup: Stackup
  reference: {
    id: string
    net_id: string
    bounds_mm: [number, number, number, number]
  }
  signals: {
    trace_id: string
    source_trace_id: string
    source_port_ids: [string, string]
    x_min_mm: number
    x_max_mm: number
    y_mm: number
    width_mm: number
    pads: {
      id: string
      x_mm: number
      y_mm: number
      width_mm: number
      height_mm: number
    }[]
  }[]
  gap_mm: number
  setup: AnalysisSetup
  notices: string[]
}

export type Preflight =
  | { status: "ready"; model: PreparedModel }
  | { status: "missing_data" | "unsupported"; issues: string[] }

export interface Runtime {
  bun?: string
  python: string
  palace: string
  /** One MPI rank and one thread; all subprocesses share this wall/RSS/disk budget. */
  seconds: number
  memory_bytes: number
  disk_bytes: number
  lock_directory: string
}

export interface AnalyzeOptions {
  output_directory: string
  setup?: AnalysisSetup
  mode: "export" | "native"
  runtime?: Runtime
  signal?: AbortSignal
}

export interface AnalysisResult {
  status:
    | "exported"
    | "native_passed"
    | "native_failed"
    | "runtime_unavailable"
    | "missing_data"
    | "unsupported"
    | "cancelled"
  output_directory?: string
  issues: string[]
  /** A prepared input is not a completed native solve. */
  native_status: "never_run" | "passed" | "failed"
  convergence_status: "not_evaluated"
}
