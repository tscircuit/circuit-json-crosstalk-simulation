import { describe, expect, test } from "bun:test"
import { createHash } from "node:crypto"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import {
  exportNativeNoiseAssets,
  nativeNoiseWaveforms,
  readNativeNoiseOutputs,
  type NativeNoiseOutputs,
  type NativeNoisePort,
} from "../index"
import { prepare } from "../lib/prepare"

const saved = new URL("../examples/results/", import.meta.url)
const circuit = await Bun.file(
  new URL("inputs/close.circuit.json", saved),
).json()
const setup = await Bun.file(
  new URL("inputs/close-channel.setup.json", saved),
).json()
const measurements = await Bun.file(new URL("measurements.json", saved)).json()
const prepared = prepare(circuit, setup)
if (prepared.status !== "ready")
  throw new Error("Archived input no longer prepares")
const preparedModel = prepared.model
const sha = (value: string) => createHash("sha256").update(value).digest("hex")

// Synthetic converter fixture, not a native qualification receipt.
function fixture(): NativeNoiseOutputs {
  return {
    circuit_json: structuredClone(circuit),
    model: structuredClone(preparedModel),
    input_sha256: sha(JSON.stringify(circuit)),
    model_sha256: sha(JSON.stringify(preparedModel)),
    samples: [1e7, 1e9].map((frequency_hz) => ({
      frequency_hz,
      real: Array.from({ length: 4 }, () => [0, 0, 0, 0]),
      imag: Array.from({ length: 4 }, () => [0, 0, 0, 0]),
    })),
    qualification: structuredClone(measurements.cases.close.checks),
    waveform: {
      times_s: [0, 1e-12, 2e-12],
      victim_source_v: [0, 1, 1],
      aggressor_source_v: [0, 0, 1],
      quiet: [0, 0.5, 0.5],
      switching: [0, 0.5, 0.6],
      noise: [0, 0, 0.1],
    },
  }
}
function ports(
  input: NativeNoiseOutputs,
  actualTaps: boolean,
): NativeNoisePort[] {
  return input.model.signals.flatMap((signal, index) =>
    [0, 1].map((end) => {
      const number = 2 * index + end + 1
      const x =
        end === 0
          ? signal.x_min_mm + setup.port_inset_mm
          : signal.x_max_mm - setup.port_inset_mm
      const template = circuit.find(
        (e: any) =>
          e.type === "pcb_port" &&
          e.layers.includes("top") &&
          e.x === (end === 0 ? -2 : 2) &&
          e.y === signal.y_mm,
      )
      const id = actualTaps
        ? `declared_native_tap_${number}`
        : template.pcb_port_id
      if (actualTaps)
        input.circuit_json = [
          ...input.circuit_json,
          { ...template, pcb_port_id: id, x },
        ]
      return {
        port_name: `P${number}`,
        signal_contact: {
          contact_type: "pcb_port" as const,
          pcb_port_id: id,
          x,
          y: signal.y_mm,
          layer: "top",
        },
        reference_contact: {
          contact_type: "pcb_copper_pour" as const,
          pcb_copper_pour_id: input.model.reference.id,
          x,
          y: signal.y_mm,
          layer: "bottom",
        },
        reference_impedance_ohms: 50,
        reference_plane: "native_internal_aperture",
        polarity: "signal_minus_reference" as const,
      }
    }),
  )
}

describe("canonical native noise interoperability", () => {
  test("the archived failed qualification remains failed, and endpoint port relabeling emits no assets", async () => {
    expect(measurements.status).toBe("convergence_checks_failed")
    expect(measurements.cli_exit_code).toBe(1)
    for (const name of ["close", "separated"])
      expect(
        measurements.cases[name].checks.full_complex_channel_mesh_status,
      ).toBe("failed")
    const input = fixture()
    let exports = 0
    const result = await exportNativeNoiseAssets(input, {
      run_id: "archived",
      observation_name: "P4 tap",
      ports: ports(input, false),
      createJsonAsset: async () => ++exports,
    })
    expect(result.status).toBe("unsupported")
    expect(result.qualification).toEqual(input.qualification)
    expect(exports).toBe(0)
    expect("network_asset" in result).toBe(false)
  })
  test("explicit aperture mapping preserves complex matrix polarity, units, all samples and unvalidated state", async () => {
    const input = fixture()
    input.samples[1]!.real[3]![1] = 0.2
    input.samples[1]!.imag[3]![1] = -0.1
    const result = await exportNativeNoiseAssets(input, {
      run_id: "synthetic",
      observation_name: "P4 tap",
      ports: ports(input, true),
      createJsonAsset: async (payload) => payload as any,
    })
    expect(result.status).toBe("exported_unvalidated")
    if (result.status !== "exported_unvalidated")
      throw new Error("Explicit aperture mapping failed")
    expect(result.validation.state).toBe("unvalidated")
    expect(result.qualification.full_complex_channel_mesh_status).toBe("failed")
    expect(result.network_asset.matrices[1][3][1]).toEqual({
      real: 0.2,
      imag: -0.1,
    })
    expect(result.network_asset.dc.kind).toBe("unavailable")
    expect(result.waveform_assets.map((a) => a.variant)).toEqual([
      "total",
      "baseline",
      "difference",
    ])
    expect(result.waveform_assets[0]!.asset.time.times_s).toEqual(
      input.waveform.times_s,
    )
    expect(result.waveform_assets[2]!.asset.values).toEqual(
      input.waveform.noise,
    )
  })
  test("refuses incomplete, nonfinite and inconsistent waveform data instead of repairing it", () => {
    const input = fixture()
    input.samples[0]!.imag.pop()
    expect(() => nativeNoiseWaveforms(input, "run", "P4")).toThrow("four-port")
    const invalid = fixture()
    invalid.waveform.quiet[1] = NaN
    expect(() => nativeNoiseWaveforms(invalid, "run", "P4")).toThrow("finite")
    const inconsistent = fixture()
    inconsistent.waveform.noise[2] = 0.2
    expect(() => nativeNoiseWaveforms(inconsistent, "run", "P4")).toThrow(
      "paired baseline",
    )
  })
  test("rejects a different reference plane even with matching aperture coordinates", async () => {
    const input = fixture()
    const mapped = ports(input, true)
    mapped[0]!.reference_plane = "endpoint_pad"
    const result = await exportNativeNoiseAssets(input, {
      run_id: "synthetic",
      observation_name: "P4 tap",
      ports: mapped,
      createJsonAsset: async () => {
        throw new Error("An unsupported map must not export")
      },
    })
    expect(result.status).toBe("unsupported")
  })
  test("reads existing native output files and rejects stale provenance or missing native completion", async () => {
    const root = await mkdtemp(join(tmpdir(), "native-noise-adapter-"))
    try {
      const input = fixture()
      const bytes = JSON.stringify(input.circuit_json)
      const raw = "native raw channel fixture"
      const summary = { checks_status: "passed", s_parameters: input.samples }
      const waveformCsv =
        "time_s,victim_source_v,aggressor_source_v,victim_quiet_v,victim_switching_v,added_noise_v\n0,0,0,0,0,0\n1e-12,1,0,0.5,0.5,0\n2e-12,1,1,0.5,0.6,0.1\n"
      const write = (path: string, value: unknown) =>
        Bun.write(
          join(root, path),
          typeof value === "string" ? value : JSON.stringify(value),
        )
      await write("eye-input.json", {
        cases: { close: { channel: "channel" } },
      })
      await write("eye-summary.json", {
        cases: {
          close: {
            checks: input.qualification,
            provenance: {
              source_csv_sha256: sha(raw),
              circuit_json_sha256: sha(bytes),
              source_summary_sha256: sha(JSON.stringify(summary)),
              source_model_sha256: sha(JSON.stringify(input.model)),
              waveform_csv_sha256: sha(waveformCsv),
            },
          },
        },
      })
      await write("channel/result.json", {
        status: "native_passed",
        native_status: "passed",
      })
      await write("channel/summary.json", summary)
      await write("channel/model.json", input.model)
      await write("channel/circuit.json", bytes)
      await write("channel/postpro/port-S.csv", raw)
      await write("close-waveforms.csv", waveformCsv)
      expect(
        (await readNativeNoiseOutputs(root, "close")).qualification,
      ).toEqual(input.qualification)
      await write("close-waveforms.csv", waveformCsv.replace("0.6", "0.7"))
      await expect(readNativeNoiseOutputs(root, "close")).rejects.toThrow(
        "waveform provenance",
      )
      await write("close-waveforms.csv", waveformCsv)
      await write("channel/summary.json", { ...summary, altered: true })
      await expect(readNativeNoiseOutputs(root, "close")).rejects.toThrow(
        "provenance",
      )
      await write("channel/summary.json", summary)
      await write("channel/postpro/port-S.csv", "changed")
      await expect(readNativeNoiseOutputs(root, "close")).rejects.toThrow(
        "provenance",
      )
      await write("channel/result.json", {
        status: "native_failed",
        native_status: "failed",
      })
      await expect(readNativeNoiseOutputs(root, "close")).rejects.toThrow(
        "completed native",
      )
    } finally {
      await rm(root, { recursive: true, force: true })
    }
  })
})
