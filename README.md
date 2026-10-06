# circuit-json-crosstalk-simulation

Run two real TSX layouts through Palace, then compare the victim eyes with the aggressor quiet and switching. The close pair has a 0.1 mm edge gap; the separated pair has a 0.8 mm gap. Trace dimensions, materials, drivers, loads, bit patterns and timing are identical. Generated inputs, meshes, logs, channels and images stay in ignored `output/`.

Install these prerequisites first:

- **Bun** (tested with 1.3.14) for the TSX renderer and TypeScript API.
- **Python 3.11+** with the packages in `python/requirements.txt`.
- **Palace** with a CPU/SuperLU build and its MPI runtime. See the [official installation instructions](https://github.com/awslabs/palace#Getting-started). This adapter was tested with Palace commit `0dc74cdf8c36c58b69b21c4a06e816048ec0b83f` (`v0.18.1-dirty`). Palace is installed separately; no solver binary is bundled or automatically installed.

```sh
bun install --frozen-lockfile
python3 -m venv .venv
.venv/bin/python -m pip install -r python/requirements.txt
export PALACE_PYTHON="$PWD/.venv/bin/python"
export PALACE_BIN="/absolute/path/to/installed/palace"
bun run simulate
```

`bun run simulate` first saves labeled PCB layouts, then runs both broadband channels on two meshes each. Open the printed `output/.../index.html` for the layouts, same-axis quiet/switching victim eyes and switching-minus-quiet noise. Native raw CSV, exact inputs, receipts, waveforms and numerical checks remain beside the plots. Each invocation creates a fresh directory. Missing software, failed native solves and unsupported geometry fail explicitly; no cached or synthetic channel replaces a solver run. Expect several minutes on a laptop.

Optional settings: `--gap 0.1 --wide-gap 0.8 --mesh-near 0.04 --output output/my-run`. Output directories must not already exist. Both layouts always run; the wider gap must exceed the close gap. Runtime paths may be omitted when the correct executables are on PATH.

The educational eye conditions are explicit in `examples/eye-setup.ts`: 1.6 Gb/s, 128 deterministic bits, 0–1.5 V ideal sources with 200 ps ramps, matched 50 Ω source/load resistors, and one common fixed timing reference. The victim travels P3 → P4; the aggressor travels P2 → P1, with a fixed half-UI phase. The matched receiver's nominal high is 0.75 V. Quiet holds the aggressor source at 0 V **on the same coupled channel**; it does not remove physical coupling. No IBIS, package, PDN, jitter, noise or equalization model is supplied.

Palace supplies the full complex four-port channel at 10 MHz and a uniform 0.25–10 GHz grid. A near-DC native check supports the separately stated ideal-PEC static limit. `python/eyes.py` fits real causal FIR taps to the two loaded voltage transfers, with the exact matched-source factor `V_receiver = 0.5 × Σ S_receiver,source V_source`. It uses linear convolution, preserves phase and never turns a single-frequency field into an eye. This is a channel-derived transient approximation, not a Palace time-domain solve or a general circuit simulator.

The report checks native matrix passivity/reciprocity, in-band loaded fit error, loaded waveform and fixed-center opening changes between meshes, time-step halving, frequency-grid decimation, 10 → 8 GHz bandwidth sensitivity and FIR support extension. The declared voltage tolerance is 5 mV. A failed loaded-voltage check stays visible and makes the CLI exit nonzero. Full complex-channel mesh criteria (0.01 absolute all-S and 5% selected coupling relative) are reported separately; they **failed** in the saved demonstration and remain visible on the page. The loaded-voltage result is not a full channel convergence proof or error bound. Air/domain convergence and a global passive/causal macromodel outside the solved band remain unevaluated. Observed finite-pattern eye openings are not BER or DDR compliance measurements. Labels describe measured spacing examples; no closed eye or uniformly better coupling is promised.

```tsx
import { simulate } from "./index"
import { renderCoupledRoutes } from "./examples/coupled-routes"
import { exampleSetup } from "./examples/setup"

const circuitJson = await renderCoupledRoutes(0.1)
const result = await simulate(circuitJson, {
  setup: exampleSetup(circuitJson),
})
console.log(result.output_directory)
```

Circuit JSON owns the actual copper XY geometry and board stackup in mm. The separate setup selects the reference copper, ports, excitation, frequency, mesh, boundaries and solver. The example supplies an explicitly assumed synthetic dielectric (Er 4, loss tangent 0 at 1 GHz) and finite copper thickness; it chooses PEC, constant Er/loss tangent, omitted soldermask and four internal 50-ohm port taps. Conductivity is preserved as physical data, but PEC omits ohmic loss. No material constants or full-board ground plane are inferred from a layer name. A stackup alone cannot yield meaningful SI results.

Supported geometry is deliberately small: a rectangular two-layer board, two straight uniform coextensive top routes with rectangular endpoint pads, and one actual rectangular bottom copper pour. The rendered example's reference copper is 5.6 × 2.0 mm, not the full 6 × 2.4 mm board. Vias, bends, tapers, holes, slots, rounded pads, stiffeners, branches, additional copper and arbitrary multilayers are rejected. Missing thickness, Er or frequency-qualified loss data must be supplied explicitly. Fields use unit incident power normalization, not an IC voltage drive.

Native completion and passivity/reciprocity do **not** establish numerical SI qualification. An earlier single-frequency weak-coupling refinement failed its relative-S criterion. The eye report evaluates the declared loaded-voltage quantities separately and retains the full complex-S change. The adapter maps mm to Palace `L0 = 0.001` m and Hz to GHz, following the [official configuration schema](https://github.com/awslabs/palace/blob/0dc74cdf8c36c58b69b21c4a06e816048ec0b83f/scripts/schema/config-schema.json). Frequency-to-time reconstruction requires broadband complex data and a DC treatment; see the [scikit-rf transform guidance](https://scikit-rf.readthedocs.io/en/latest/api/generated/skrf.network.Network.impulse_response.html). This code uses its own causal loaded FIR fit, not scikit-rf.

For the older single-frequency field/port inspector, run `bun run serve` and open http://127.0.0.1:3217. It binds loopback, validates requests and supports cancellation. That inspector does not generate eyes; the new CLI report is the two-layout eye demo.

Each native channel uses one rank/thread, a 600-second wall limit, 6 GiB aggregate RSS and 256 MiB output limit. The four channels run sequentially. An owner-token lock serializes native jobs; occupied locks are preserved. `PALACE_HEAVY_LOCK` can override the default `/tmp/dot-cloud-heavy.lock`. Ctrl-C stops the CLI through the Python supervisor; API callers can supply an `AbortSignal`.

Local checks: `bun test`, `bun run typecheck`, `bun run format:check`, and `python -m unittest discover -s python -p 'test_eyes.py'`. Tests cover rendered geometry, units, preflight, native-only API behavior, HTTP boundaries, matched voltage scaling, causal response and zero-mutual controls. They do not launch Palace.

MIT adapter code. Geometry/contact and field-reader methods were adapted from `tscircuit/simulate-return-current` under the same license. Palace is a separate Apache-2.0 runtime. Experimental renderer/schema dependencies are pinned in `bun.lock`; no schema or analyzer changes are included.
