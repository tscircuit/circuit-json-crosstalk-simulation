import { createHash } from "node:crypto"
import { resolve } from "node:path"
import type { CircuitJson, PreparedModel } from "./types"

type Contact = { x: number; y: number; layer: string } & (
  | { contact_type: "pcb_port"; pcb_port_id: string }
  | { contact_type: "pcb_copper_pour"; pcb_copper_pour_id: string }
)
export interface NativeNoisePort {
  port_name: string
  signal_contact: Contact
  reference_contact: Contact
  reference_impedance_ohms: number
  reference_plane: string
  polarity: "signal_minus_reference"
}
type NativeSample = { frequency_hz: number; real: number[][]; imag: number[][] }
export interface NativeNoiseOutputs {
  circuit_json: CircuitJson
  model: PreparedModel
  input_sha256: string
  model_sha256: string
  samples: NativeSample[]
  /** Kept verbatim, including failed full-channel mesh qualification. */
  qualification: Record<string, unknown>
  waveform: {
    times_s: number[]
    victim_source_v: number[]
    aggressor_source_v: number[]
    quiet: number[]
    switching: number[]
    noise: number[]
  }
}

const hash = (bytes: string | Uint8Array) =>
  createHash("sha256").update(bytes).digest("hex")
const shaPattern = /^[a-f0-9]{64}$/

/** Consume the existing native summary and full-resolution eyes.py CSV, without a solve or refit. */
export async function readNativeNoiseOutputs(
  directory: string,
  caseName: string,
): Promise<NativeNoiseOutputs> {
  const root = resolve(directory)
  const input = await Bun.file(resolve(root, "eye-input.json")).json()
  const report = await Bun.file(resolve(root, "eye-summary.json")).json()
  const paths = input.cases[caseName]
  const caseReport = report.cases[caseName]
  if (!paths || !caseReport) throw new Error("Unknown native eye case")
  const channel = resolve(root, paths.channel)
  const result = await Bun.file(resolve(channel, "result.json")).json()
  const summaryBytes = await Bun.file(resolve(channel, "summary.json")).text()
  const summary = JSON.parse(summaryBytes)
  if (
    result.status !== "native_passed" ||
    result.native_status !== "passed" ||
    summary.checks_status !== "passed"
  )
    throw new Error(
      "Canonical exports require completed native channel outputs; there is no fallback",
    )
  const circuitBytes = await Bun.file(resolve(channel, "circuit.json")).text()
  const modelBytes = await Bun.file(resolve(channel, "model.json")).text()
  const raw = new Uint8Array(
    await Bun.file(resolve(channel, "postpro/port-S.csv")).arrayBuffer(),
  )
  if (
    hash(raw) !== caseReport.provenance.source_csv_sha256 ||
    hash(circuitBytes) !== caseReport.provenance.circuit_json_sha256 ||
    hash(summaryBytes) !== caseReport.provenance.source_summary_sha256 ||
    hash(modelBytes) !== caseReport.provenance.source_model_sha256
  )
    throw new Error("Native eye provenance does not match the channel inputs")
  const waveformBytes = await Bun.file(
    resolve(root, `${caseName}-waveforms.csv`),
  ).text()
  if (hash(waveformBytes) !== caseReport.provenance.waveform_csv_sha256)
    throw new Error(
      "Native waveform provenance does not match the saved capture",
    )
  const csv = waveformBytes.trim().split(/\r?\n/)
  if (
    csv.shift() !==
    "time_s,victim_source_v,aggressor_source_v,victim_quiet_v,victim_switching_v,added_noise_v"
  )
    throw new Error("Unexpected native waveform columns")
  const rows = csv.map((line) => line.split(",").map(Number))
  if (
    rows.some((row) => row.length !== 6 || row.some((v) => !Number.isFinite(v)))
  )
    throw new Error("Malformed native waveform CSV")
  const column = (i: number) => rows.map((row) => row[i]!)
  return {
    circuit_json: JSON.parse(circuitBytes),
    model: JSON.parse(modelBytes),
    input_sha256: hash(circuitBytes),
    model_sha256: hash(modelBytes),
    samples: summary.s_parameters,
    qualification: structuredClone(caseReport.checks),
    waveform: {
      times_s: column(0),
      victim_source_v: column(1),
      aggressor_source_v: column(2),
      quiet: column(3),
      switching: column(4),
      noise: column(5),
    },
  }
}

function validateOutputs(input: NativeNoiseOutputs) {
  if (
    !shaPattern.test(input.input_sha256) ||
    !shaPattern.test(input.model_sha256)
  )
    throw new Error("Explicit input and model hashes are required")
  const samples = [...input.samples].sort(
    (a, b) => a.frequency_hz - b.frequency_hz,
  )
  if (
    !samples.length ||
    samples.some(
      (sample, index) =>
        !Number.isFinite(sample.frequency_hz) ||
        sample.frequency_hz <= 0 ||
        (index > 0 &&
          sample.frequency_hz <= samples[index - 1]!.frequency_hz) ||
        [sample.real, sample.imag].some(
          (matrix) =>
            matrix.length !== 4 ||
            matrix.some(
              (row) => row.length !== 4 || row.some((v) => !Number.isFinite(v)),
            ),
        ),
    )
  )
    throw new Error(
      "Expected complete finite native four-port matrices at distinct positive frequencies",
    )
  const w = input.waveform
  if (
    w.times_s.length < 2 ||
    w.times_s.some(
      (t, i) => !Number.isFinite(t) || (i > 0 && t <= w.times_s[i - 1]!),
    ) ||
    Object.values(w).some(
      (values) =>
        values.length !== w.times_s.length ||
        values.some((v) => !Number.isFinite(v)),
    )
  )
    throw new Error(
      "Native waveform samples must be complete, finite and ordered in seconds",
    )
  if (
    w.noise.some((v, i) => Math.abs(w.switching[i]! - w.quiet[i]! - v) > 1e-10)
  )
    throw new Error("Native paired baseline does not reproduce added noise")
  return samples
}

/** Scalar canonical waveform payloads are raw artifacts, never a completed/validated result. */
export function nativeNoiseWaveforms(
  input: NativeNoiseOutputs,
  runId: string,
  observationName: string,
) {
  const samples = validateOutputs(input)
  if (!runId || !observationName)
    throw new Error("Run and observation identities are required")
  const w = input.waveform
  return (
    [
      ["total", "switching"],
      ["baseline", "quiet"],
      ["difference", "noise"],
    ] as const
  ).map(([variant, key]) => ({
    format: "simulation_pcb_noise_waveform_json_v1" as const,
    run_id: runId,
    observation_name: observationName,
    unit: "V" as const,
    variant,
    full_resolution: true as const,
    time: { kind: "explicit" as const, times_s: w.times_s.slice() },
    values: w[key].slice(),
    valid_intervals_s: [{ start_s: w.times_s[0]!, end_s: w.times_s.at(-1)! }],
    bandwidth_hz: samples.at(-1)!.frequency_hz,
    input_sha256: input.input_sha256,
    source_sha256: hash(
      JSON.stringify({
        times_s: w.times_s,
        victim_source_v: w.victim_source_v,
        aggressor_source_v:
          variant === "baseline"
            ? w.aggressor_source_v.map(() => 0)
            : w.aggressor_source_v,
      }),
    ),
  }))
}

/** Native apertures are internal taps. Endpoint pad/port labels are deliberately rejected. */
function mapPorts(input: NativeNoiseOutputs, ports: NativeNoisePort[]) {
  const { model, circuit_json: circuit } = input
  return (
    ports.length === 4 &&
    ports.every((port, index) => {
      const signal = model.signals[Math.floor(index / 2)]!
      const x =
        index % 2 === 0
          ? signal.x_min_mm + model.setup.port_inset_mm
          : signal.x_max_mm - model.setup.port_inset_mm
      const s = port.signal_contact,
        r = port.reference_contact
      const physical =
        s.contact_type === "pcb_port"
          ? circuit.find(
              (e) => e.type === "pcb_port" && e.pcb_port_id === s.pcb_port_id,
            )
          : undefined
      const reference =
        r.contact_type === "pcb_copper_pour"
          ? circuit.find(
              (e) =>
                e.type === "pcb_copper_pour" &&
                e.pcb_copper_pour_id === r.pcb_copper_pour_id,
            )
          : undefined
      return (
        port.port_name === `P${index + 1}` &&
        port.reference_impedance_ohms === model.setup.port_resistance_ohms &&
        port.polarity === "signal_minus_reference" &&
        port.reference_plane === "native_internal_aperture" &&
        physical?.type === "pcb_port" &&
        reference?.type === "pcb_copper_pour" &&
        reference.pcb_copper_pour_id === model.reference.id &&
        reference.layer === r.layer &&
        s.layer === "top" &&
        physical.layers.includes("top") &&
        [
          s.x - x,
          s.y - signal.y_mm,
          r.x - x,
          r.y - signal.y_mm,
          physical.x - x,
          physical.y - signal.y_mm,
        ].every((v) => Number.isFinite(v) && Math.abs(v) < 1e-9)
      )
    })
  )
}

/** Inject the shared createJsonAsset helper; this repository has no dependency on the analysis engine. */
export async function exportNativeNoiseAssets<Asset>(
  input: NativeNoiseOutputs,
  options: {
    run_id: string
    observation_name: string
    ports: NativeNoisePort[]
    createJsonAsset: (
      payload: unknown,
      options: { projectRelativePath: string },
    ) => Promise<Asset>
  },
) {
  const samples = validateOutputs(input)
  const qualification = structuredClone(input.qualification)
  if (!mapPorts(input, options.ports))
    return {
      status: "unsupported" as const,
      qualification,
      diagnostics: [
        {
          code: "native_tap_contacts_required",
          message:
            "Four actual PCB port contacts at the native internal apertures and the exact reference plane are required; endpoint ports cannot label this channel.",
        },
      ],
    }
  const network = {
    format: "simulation_pcb_noise_network_json_v1",
    run_id: options.run_id,
    input_sha256: input.input_sha256,
    model_sha256: input.model_sha256,
    ports: structuredClone(options.ports),
    frequencies_hz: samples.map((sample) => sample.frequency_hz),
    representation: "s",
    matrix_units: "dimensionless",
    matrices: samples.map((sample) =>
      sample.real.map((row, i) =>
        row.map((real, j) => ({ real, imag: sample.imag[i]![j]! })),
      ),
    ),
    phasor_convention: "exp_positive_j_omega_t",
    current_sign_convention: "into_pcb",
    dc: {
      kind: "unavailable",
      reason:
        "The native sweep has no DC solve; the eye FIR's topology-derived DC constraint is not an extracted DC matrix.",
    },
    extraction: {
      provider: "circuit-json-crosstalk-simulation/palace",
      version: "noise-adapter-v1",
      normalization: "power_waves_real_positive_z0",
    },
  }
  const network_asset = await options.createJsonAsset(network, {
    projectRelativePath: "noise/network.json",
  })
  const waveform_assets = []
  for (const waveform of nativeNoiseWaveforms(
    input,
    options.run_id,
    options.observation_name,
  ))
    waveform_assets.push({
      observation_name: options.observation_name,
      variant: waveform.variant,
      asset: await options.createJsonAsset(waveform, {
        projectRelativePath: `noise/${waveform.variant}.json`,
      }),
    })
  return {
    status: "exported_unvalidated" as const,
    validation: { state: "unvalidated" as const },
    qualification,
    network_asset,
    waveform_assets,
  }
}
