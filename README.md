# circuit-json-crosstalk-simulation

Render the included TSX example to Circuit JSON, then run a real Palace simulation. The exported `simulate` function does the same for supplied Circuit JSON. Generated inputs, meshes, logs, S parameters and field images stay in ignored `output/`; no run bundles or CI workflows are committed.

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

`bun run simulate` renders the 0.1 mm gap example and runs the native solver. It prints the result and output directory, then exits. Each invocation creates a fresh directory under `output/`. Missing software, incomplete physical data and unsupported geometry fail explicitly; there is no synthetic solver fallback.

Optional example settings: `bun run simulate --gap 0.8`, or `bun run simulate --mesh-near 0.06 --output output/refined`. Explicit output directories must not already exist. `PALACE_BIN`/`PALACE_PYTHON` may be omitted when the correct `palace`/`python3` executables are on PATH.

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

Native completion and passivity/reciprocity checks do **not** establish mesh or truncation convergence. A previous example refinement changed selected complex coupling by 75.19%, exceeding its 5% limit; truncation was not evaluated. Results remain preliminary, not SI/DDR timing qualification. The adapter maps mm to Palace `L0 = 0.001` m and Hz to GHz, following the [official configuration schema](https://github.com/awslabs/palace/blob/0dc74cdf8c36c58b69b21c4a06e816048ec0b83f/scripts/schema/config-schema.json).

For an optional local page, run `bun run serve` with the same prerequisites and open http://127.0.0.1:3217. It calls `simulate`, offers fixed examples, shows actual logs/results and supports cancellation. It binds loopback, validates requests and serves only owned files. UI output goes to `output/web/`.

Runs use one rank/thread, a 90-second wall limit, 6 GiB aggregate RSS and 256 MiB output limit. An owner-token lock serializes native jobs; occupied locks are preserved. `PALACE_HEAVY_LOCK` can override the default `/tmp/dot-cloud-heavy.lock`. To stop a CLI job, use Ctrl-C; API callers can supply an `AbortSignal`.

Local checks: `bun test`, `bun run typecheck`, `bun run format:check`. Tests cover rendered geometry, units, missing/unsupported input, native-only API behavior and the optional HTTP wrapper; they do not launch Palace.

MIT adapter code. Geometry/contact and field-reader methods were adapted from `tscircuit/simulate-return-current` under the same license. Palace is a separate Apache-2.0 runtime. Experimental renderer/schema dependencies are pinned in `bun.lock`; no schema or analyzer changes are included.
