import { any_circuit_element } from "circuit-json"
import type {
  AnalysisSetup,
  CircuitJson,
  PreparedModel,
  Preflight,
  Stackup,
} from "./types"

class InputProblem extends Error {
  constructor(
    public status: "missing_data" | "unsupported",
    message: string,
  ) {
    super(message)
  }
}
const unsupported = (message: string): never => {
  throw new InputProblem("unsupported", message)
}
const missing = (message: string): never => {
  throw new InputProblem("missing_data", message)
}
const close = (a: number, b: number) => Math.abs(a - b) < 1e-9
function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value))
    return missing(`${label} must be supplied as a finite number`)
  return value
}
function positive(value: unknown, label: string): number {
  const n = finite(value, label)
  if (n <= 0) return unsupported(`${label} must be positive`)
  return n
}
function nonnegative(value: unknown, label: string): number {
  const n = finite(value, label)
  if (n < 0) return unsupported(`${label} must be nonnegative`)
  return n
}
function rect(
  points: readonly { x: number; y: number; bulge?: number }[],
): [number, number, number, number] {
  if (
    points.length !== 4 ||
    points.some(
      (p) =>
        !Number.isFinite(p.x) || !Number.isFinite(p.y) || (p.bulge ?? 0) !== 0,
    )
  )
    return unsupported(
      "Reference copper requires four finite straight rectangle vertices",
    )
  const xs = [...new Set(points.map((p) => p.x))],
    ys = [...new Set(points.map((p) => p.y))]
  if (
    xs.length !== 2 ||
    ys.length !== 2 ||
    points.some((p, i) => {
      const q = points[(i + 1) % 4]
      return (p.x === q.x) === (p.y === q.y)
    })
  )
    return unsupported(
      "Reference copper must be an axis-aligned rectangle without crossed edges",
    )
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
}
function contains(bounds: number[], x: number, y: number, hx = 0, hy = hx) {
  return (
    x - hx >= bounds[0] - 1e-9 &&
    x + hx <= bounds[2] + 1e-9 &&
    y - hy >= bounds[1] - 1e-9 &&
    y + hy <= bounds[3] + 1e-9
  )
}

/** Partial physical data is valid Circuit JSON; this adapter requires a resolved model. */
function resolveStackup(
  original: Stackup | undefined,
  fill: Stackup | undefined,
): Stackup {
  if (!original && !fill)
    return missing(
      "pcb_board.stackup is unknown; supply an explicit stackup_fill",
    )
  const base = structuredClone(original ?? fill!)
  if (fill) {
    if (!["specified", "assumed"].includes(fill.source))
      return unsupported(
        "stackup_fill requires valid specified/assumed provenance",
      )
    const probe = any_circuit_element.safeParse({
      type: "pcb_board",
      pcb_board_id: "fill_validation",
      center: { x: 0, y: 0 },
      width: 1,
      height: 1,
      num_layers: 2,
      thickness: 1,
      stackup: fill,
    })
    if (!probe.success) return unsupported("Invalid stackup_fill physical data")
  }
  let suppliedFill = !original && !!fill
  if (original && fill) {
    if (original.layers.length !== fill.layers.length)
      return unsupported("stackup_fill cannot change the owning layer sequence")
    for (let i = 0; i < base.layers.length; i++) {
      const target = base.layers[i] as unknown as Record<string, unknown>
      for (const [key, value] of Object.entries(fill.layers[i])) {
        if (
          target[key] !== undefined &&
          JSON.stringify(target[key]) !== JSON.stringify(value)
        )
          return unsupported(
            `stackup_fill cannot override supplied layers[${i}].${key}`,
          )
        if (target[key] === undefined && value !== undefined)
          suppliedFill = true
        target[key] = value
      }
    }
    // Preserve original Circuit JSON separately, but do not mislabel assumed fills.
  }
  if (suppliedFill && fill?.source === "assumed") base.source = "assumed"
  if (!["specified", "assumed"].includes(base.source))
    return missing("Stackup requires explicit specified/assumed provenance")
  const [top, dielectric, bottom] = base.layers
  if (
    base.layers.length !== 3 ||
    top?.type !== "copper" ||
    top.layer !== "top" ||
    dielectric?.type !== "dielectric" ||
    bottom?.type !== "copper" ||
    bottom.layer !== "bottom"
  )
    return unsupported(
      "Only ordered top copper / one dielectric / bottom copper is supported",
    )
  base.layers.forEach((layer, i) =>
    positive(layer.thickness_mm, `stackup layer ${i} thickness_mm`),
  )
  for (const layer of [top, bottom])
    if (layer.conductivity_s_per_m !== undefined)
      positive(
        layer.conductivity_s_per_m,
        `${layer.layer} conductivity_s_per_m`,
      )
  positive(dielectric.dielectric_constant, "dielectric_constant")
  positive(
    dielectric.dielectric_constant_frequency_hz,
    "dielectric_constant_frequency_hz",
  )
  nonnegative(
    dielectric.dielectric_loss_tangent,
    "dielectric_loss_tangent (zero must also be explicit)",
  )
  positive(
    dielectric.dielectric_loss_tangent_frequency_hz,
    "dielectric_loss_tangent_frequency_hz",
  )
  return base
}

function checkSetup(s: AnalysisSetup) {
  if (
    s.conductor_model !== "pec" ||
    s.dielectric_model !== "constant_er_and_loss_tangent" ||
    s.relative_permeability !== 1 ||
    s.soldermask !== "omitted"
  )
    unsupported(
      "Explicit PEC, constant Er/tan-delta, nonmagnetic material and omitted soldermask choices are required",
    )
  if (
    s.boundary !== "first_order_absorbing" ||
    s.solver?.order !== 1 ||
    s.solver?.type !== "SuperLU"
  )
    unsupported(
      "Only first-order absorbing boundaries, order-1 elements and SuperLU are supported",
    )
  positive(s.port_inset_mm, "port_inset_mm")
  positive(s.port_resistance_ohms, "port_resistance_ohms")
  positive(s.air_padding_mm, "air_padding_mm")
  for (const key of ["near_mm", "far_mm", "transition_mm"] as const)
    positive(s.mesh?.[key], `mesh.${key}`)
  if (s.mesh.near_mm > s.mesh.far_mm)
    unsupported("mesh.near_mm must not exceed far_mm")
  positive(s.solver.tolerance, "solver.tolerance")
  if (
    s.solver.tolerance >= 1 ||
    !Number.isInteger(s.solver.max_iterations) ||
    s.solver.max_iterations <= 0
  )
    unsupported(
      "Solver tolerance must be < 1 and max_iterations a positive integer",
    )
  for (const key of [
    "passivity_tolerance",
    "reciprocity_tolerance",
    "convergence_relative",
    "convergence_absolute",
  ] as const)
    nonnegative(s.checks?.[key], `checks.${key}`)
  if (
    !Array.isArray(s.frequency_hz) ||
    !s.frequency_hz.length ||
    s.frequency_hz.length > 8
  )
    unsupported("Supply 1–8 explicit frequency_hz values")
  s.frequency_hz.forEach((f) => positive(f, "frequency_hz"))
  if (new Set(s.frequency_hz).size !== s.frequency_hz.length)
    unsupported("Duplicate frequency samples")
  if (
    !Array.isArray(s.save_fields_at_hz) ||
    s.save_fields_at_hz.length > 1 ||
    s.save_fields_at_hz.some((f) => !s.frequency_hz.includes(f))
  )
    unsupported(
      "save_fields_at_hz must contain at most one value from frequency_hz in this bounded adapter",
    )
  if (
    !Array.isArray(s.trace_ids) ||
    s.trace_ids.length !== 2 ||
    new Set(s.trace_ids).size !== 2
  )
    unsupported("Select two distinct trace_ids explicitly")
}

/** No geometry is patched, flattened, or synthesized after rendering. */
export function prepare(
  circuitJson: CircuitJson,
  setup?: AnalysisSetup,
): Preflight {
  try {
    if (!setup)
      return missing(
        "Analysis setup is required; a stackup alone does not define a simulation",
      )
    checkSetup(setup)
    const consumedTypes = new Set([
      "pcb_board",
      "pcb_trace",
      "pcb_port",
      "pcb_smtpad",
      "pcb_copper_pour",
      "source_trace",
      "source_port",
      "source_net",
    ])
    for (const element of circuitJson.filter((e) =>
      consumedTypes.has(e.type),
    )) {
      const parsed = any_circuit_element.safeParse(element)
      if (!parsed.success)
        unsupported(`Invalid ${element.type} Circuit JSON record`)
    }
    const seen = new Set<string>()
    for (const e of circuitJson) {
      const id = (e as unknown as Record<string, unknown>)[`${e.type}_id`]
      if (typeof id === "string") {
        if (seen.has(`${e.type}:${id}`))
          unsupported(`Duplicate ${e.type} identity ${id}`)
        seen.add(`${e.type}:${id}`)
      }
      if (
        [
          "pcb_via",
          "pcb_hole",
          "pcb_plated_hole",
          "pcb_cutout",
          "pcb_ground_plane_region",
          "pcb_copper_text",
          "pcb_copper_polygon",
          "pcb_stiffener",
        ].includes(e.type)
      )
        unsupported(
          `${e.type} geometry is not supported by the straight-pair adapter`,
        )
      if (e.type.endsWith("_error"))
        unsupported(`Rendered Circuit JSON contains ${e.type}`)
    }
    const boards = circuitJson.filter((e) => e.type === "pcb_board")
    if (boards.length !== 1)
      return unsupported("Exactly one pcb_board is required")
    const board = boards[0]
    if (board.num_layers !== 2 || board.outline?.length)
      return unsupported(
        "Only one rectangular two-layer board without outline/slots is supported",
      )
    const w = positive(board.width, "board.width"),
      h = positive(board.height, "board.height")
    const cx = finite(board.center.x, "board.center.x"),
      cy = finite(board.center.y, "board.center.y")
    const bounds: [number, number, number, number] = [
      cx - w / 2,
      cy - h / 2,
      cx + w / 2,
      cy + h / 2,
    ]
    const stackup = resolveStackup(board.stackup, setup.stackup_fill)
    const thickness = stackup.layers.reduce(
      (sum, l) => sum + l.thickness_mm!,
      0,
    )
    if (!close(positive(board.thickness, "board.thickness"), thickness))
      unsupported("Board thickness does not equal the explicit stackup sum")
    const pours = circuitJson.filter((e) => e.type === "pcb_copper_pour")
    if (
      pours.length !== 1 ||
      pours[0].pcb_copper_pour_id !== setup.reference_copper_id
    )
      return unsupported(
        "Select the sole actual reference copper pour; additional pours are unsupported",
      )
    const pour = pours[0]
    if (
      pour.layer !== "bottom" ||
      pour.source_net_id !== setup.reference_net_id ||
      !circuitJson.some(
        (e) =>
          e.type === "source_net" && e.source_net_id === setup.reference_net_id,
      )
    )
      return unsupported(
        "Reference copper layer/net identity does not match the selected bottom reference net",
      )
    let reference: [number, number, number, number]
    if (pour.shape === "rect") {
      if ((pour.rotation ?? 0) % 180 !== 0)
        return unsupported("Rotated reference copper is unsupported")
      const pw = positive(pour.width, "reference.width"),
        ph = positive(pour.height, "reference.height")
      reference = [
        pour.center.x - pw / 2,
        pour.center.y - ph / 2,
        pour.center.x + pw / 2,
        pour.center.y + ph / 2,
      ]
    } else if (pour.shape === "polygon") reference = rect(pour.points)
    else if (pour.shape === "brep" && pour.brep_shape.inner_rings.length === 0)
      reference = rect(pour.brep_shape.outer_ring.vertices)
    else
      return unsupported(
        "Reference holes, curved edges and nonrectangular pours are unsupported",
      )
    if (
      !contains(bounds, reference[0], reference[1]) ||
      !contains(bounds, reference[2], reference[3])
    )
      return unsupported("Reference copper extends beyond the actual board")
    const traces = circuitJson.filter((e) => e.type === "pcb_trace")
    if (
      traces.length !== 2 ||
      setup.trace_ids.some((id) => !traces.some((t) => t.pcb_trace_id === id))
    )
      return unsupported("Exactly the two selected physical traces must exist")
    const ports = circuitJson.filter((e) => e.type === "pcb_port")
    const sources = circuitJson.filter((e) => e.type === "source_trace")
    const pads = circuitJson.filter((e) => e.type === "pcb_smtpad")
    const consumedPads = new Set<string>()
    const signals: PreparedModel["signals"] = setup.trace_ids.map((id) => {
      const trace = traces.find((t) => t.pcb_trace_id === id)!
      const route = trace.route
      if (
        route.length < 2 ||
        route.some(
          (p) =>
            p.route_type !== "wire" ||
            p.layer !== "top" ||
            "start_width" in p ||
            "end_width" in p,
        )
      )
        return unsupported(
          "Only uniform top-layer wire routes are supported; vias, through-pads and tapers fail",
        )
      const wire = route as Extract<
        (typeof route)[number],
        { route_type: "wire" }
      >[]
      const first = wire[0],
        last = wire.at(-1)!
      const width = positive(first.width, `${id}.width`)
      wire.forEach((p) => {
        finite(p.x, `${id}.x`)
        finite(p.y, `${id}.y`)
        positive(p.width, `${id}.width`)
      })
      if (
        wire.some((p) => !close(p.y, first.y) || !close(p.width, width)) ||
        close(first.x, last.x)
      )
        return unsupported(
          "Only straight horizontal nonzero uniform routes are supported",
        )
      const direction = Math.sign(last.x - first.x)
      if (
        wire.some((p, i) => i > 0 && (p.x - wire[i - 1].x) * direction < -1e-9)
      )
        return unsupported(
          "Route doubles back; duplicate final endpoint is allowed",
        )
      const endpoints = [first, last].map((point, end) => {
        const portId = end ? point.end_pcb_port_id : point.start_pcb_port_id
        const matches = ports.filter(
          (p) =>
            p.pcb_port_id === portId &&
            close(p.x, point.x) &&
            close(p.y, point.y) &&
            p.layers.length === 1 &&
            p.layers[0] === "top",
        )
        if (matches.length !== 1)
          return missing(`${id} endpoint requires one matching top pcb_port`)
        return matches[0]
      })
      const source = sources.find(
        (s) => s.source_trace_id === trace.source_trace_id,
      )
      if (
        !source ||
        source.connected_source_port_ids.length !== 2 ||
        (source.connected_source_net_ids?.length ?? 0) !== 0 ||
        endpoints.some(
          (p) => !source.connected_source_port_ids.includes(p.source_port_id),
        ) ||
        sources.some(
          (s) =>
            s !== source &&
            s.connected_source_port_ids.some((p) =>
              source.connected_source_port_ids.includes(p),
            ),
        )
      )
        return unsupported(
          "Selected route must map to one unbranched two-port source trace",
        )
      const signalPads = endpoints.map((port) => {
        if (
          !circuitJson.some(
            (e) =>
              e.type === "source_port" &&
              e.source_port_id === port.source_port_id,
          )
        )
          return unsupported("Endpoint source_port is missing")
        const found = pads.filter((p) => p.pcb_port_id === port.pcb_port_id)
        const pad = found[0]
        if (
          found.length !== 1 ||
          pad.shape !== "rect" ||
          pad.layer !== "top" ||
          ((pad as unknown as { ccw_rotation?: number }).ccw_rotation ?? 0) %
            180 !==
            0
        )
          return unsupported(
            "Each signal endpoint requires one unrotated rectangular top pad",
          )
        const extra = pad as unknown as {
          corner_radius?: number
          rect_border_radius?: number
        }
        if (
          (extra.corner_radius ?? 0) !== 0 ||
          (extra.rect_border_radius ?? 0) !== 0
        )
          unsupported("Rounded rectangular pad corners are unsupported")
        if (
          !close(pad.x, port.x) ||
          !close(pad.y, port.y) ||
          !close(pad.height, width)
        )
          return unsupported(
            "Endpoint pad must be centered on the route and have its width across Y",
          )
        positive(pad.width, "pad.width")
        if (pad.width < width)
          unsupported(
            "Endpoint pad must cover the round trace cap; smaller pads are unsupported",
          )
        if (!contains(bounds, pad.x, pad.y, pad.width / 2, pad.height / 2))
          unsupported("Pad outside board")
        consumedPads.add(pad.pcb_smtpad_id)
        return {
          id: pad.pcb_smtpad_id,
          x_mm: pad.x,
          y_mm: pad.y,
          width_mm: pad.width,
          height_mm: pad.height,
        }
      })
      const xmin = Math.min(first.x, last.x),
        xmax = Math.max(first.x, last.x)
      if (
        !contains(reference, xmin, first.y, width / 2) ||
        !contains(reference, xmax, first.y, width / 2)
      )
        unsupported(
          "Actual bottom reference copper must cover both routes including round ends",
        )
      if (
        setup.port_inset_mm <=
          Math.max(...signalPads.map((p) => p.width_mm / 2)) ||
        setup.port_inset_mm * 2 >= xmax - xmin
      )
        unsupported(
          "Internal taps must lie inside the straight route beyond endpoint pads",
        )
      return {
        trace_id: id,
        source_trace_id: source.source_trace_id,
        source_port_ids: (direction > 0
          ? endpoints
          : endpoints.toReversed()
        ).map((p) => p.source_port_id) as [string, string],
        x_min_mm: xmin,
        x_max_mm: xmax,
        y_mm: first.y,
        width_mm: width,
        pads: signalPads,
      }
    })
    if (new Set(signals.flatMap((s) => s.source_port_ids)).size !== 4)
      return unsupported("The two signals share an endpoint")
    const [a, b] = signals
    if (!close(a.x_min_mm, b.x_min_mm) || !close(a.x_max_mm, b.x_max_mm))
      return unsupported("Routes must be coextensive in X")
    const gap = Math.abs(a.y_mm - b.y_mm) - (a.width_mm + b.width_mm) / 2
    positive(gap, "Actual signal copper gap")
    for (const pad of pads.filter((p) => !consumedPads.has(p.pcb_smtpad_id))) {
      const extra = pad as unknown as {
        corner_radius?: number
        rect_border_radius?: number
      }
      if (
        (extra.corner_radius ?? 0) !== 0 ||
        (extra.rect_border_radius ?? 0) !== 0
      )
        unsupported("Rounded rectangular pad corners are unsupported")
      if (
        pad.layer !== "bottom" ||
        pad.shape !== "rect" ||
        ((pad as unknown as { ccw_rotation?: number }).ccw_rotation ?? 0) %
          180 !==
          0 ||
        !contains(
          reference,
          pad.x,
          pad.y,
          positive(pad.width, "bottom pad.width") / 2,
          positive(pad.height, "bottom pad.height") / 2,
        )
      )
        return unsupported(
          "Additional copper is unsupported; bottom pads must be wholly inside the selected reference",
        )
      const port = ports.find((p) => p.pcb_port_id === pad.pcb_port_id)
      if (
        !port ||
        !sources.some(
          (s) =>
            s.connected_source_port_ids.includes(port.source_port_id) &&
            s.connected_source_net_ids?.includes(setup.reference_net_id),
        )
      )
        return unsupported(
          "Bottom pad lacks actual source connectivity to the selected reference net",
        )
    }
    return {
      status: "ready",
      model: {
        units: {
          geometry: "mm",
          frequency: "Hz",
          palace_length_scale_m: 0.001,
        },
        board: {
          id: board.pcb_board_id,
          bounds_mm: bounds,
          thickness_mm: Number(thickness.toFixed(12)),
        },
        stackup,
        reference: {
          id: pour.pcb_copper_pour_id,
          net_id: setup.reference_net_id,
          bounds_mm: reference,
        },
        signals,
        gap_mm: gap,
        setup: structuredClone(setup),
        notices: [
          "PEC is an explicit analysis approximation: supplied conductivity is preserved but ohmic copper loss is omitted.",
          "Er and loss tangent are held constant at the supplied values; their distinct physical reference frequencies are preserved.",
          "Nonmagnetic materials and omitted soldermask are explicit setup assumptions. Board material labels do not supply constants.",
          `Four internal lumped taps leave open end stubs; ${setup.port_resistance_ohms}-ohm normalization is not an IC driver/load model.`,
          "Finite board and actual reference-pour boundaries are retained. No whole-board ground is synthesized.",
        ],
      },
    }
  } catch (error) {
    return {
      status: error instanceof InputProblem ? error.status : "unsupported",
      issues: [error instanceof Error ? error.message : String(error)],
    }
  }
}
