import { resolve } from "node:path"
import {
  createGeometryModel,
  createMeshRequirements,
  exportGmsh,
  parseCircuitJson,
  type FabricationStackup,
} from "circuit-json-to-gmsh/lib/index"
import type { CircuitJson, PreparedModel } from "./types"

/** Map owning physical data explicitly. Never use the exporter's loss default. */
export function boardGeometry(
  circuitJson: CircuitJson,
  prepared: PreparedModel,
) {
  for (const layer of prepared.stackup.layers) {
    if (!Number.isFinite(layer.thickness_mm) || layer.thickness_mm! <= 0)
      throw new Error("Explicit physical thickness is required")
    if (
      layer.type === "dielectric" &&
      (!Number.isFinite(layer.dielectric_constant) ||
        !Number.isFinite(layer.dielectric_loss_tangent))
    )
      throw new Error(
        "Explicit Er and loss are required; exporter defaults are not material evidence",
      )
  }
  const stackup: FabricationStackup = {
    nominalBoardThicknessMm: prepared.board.thickness_mm,
    layers: prepared.stackup.layers.map((layer) =>
      layer.type === "copper"
        ? { name: layer.layer, copperThicknessMm: layer.thickness_mm! }
        : {
            material: layer.material ?? "Explicitly supplied dielectric",
            dielectricThicknessMm: layer.thickness_mm!,
            dielectricConstant: layer.dielectric_constant!,
            lossTangent: layer.dielectric_loss_tangent!,
          },
    ),
  }
  const parsed = parseCircuitJson(circuitJson)
  const model = createGeometryModel({ circuitJson: parsed, stackup })
  const alias = (id: string) => {
    const port = parsed.find(
      (e) => e.type === "source_port" && e.source_port_id === id,
    )
    if (!port || port.type !== "source_port")
      throw new Error("Missing source endpoint")
    const component = parsed.find(
      (e) =>
        e.type === "source_component" &&
        e.source_component_id === port.source_component_id,
    )
    if (!component || component.type !== "source_component")
      throw new Error("Missing source component")
    return `${component.name}.${port.name}`
  }
  const connections = prepared.signals.map((s) => ({
    from: alias(s.source_port_ids[0]),
    to: alias(s.source_port_ids[1]),
  }))
  const requirements = createMeshRequirements({
    circuitJson: parsed,
    model,
    connections,
  })
  const signal_net_ids = connections.map(
    (c) => requirements.terminals.find((t) => t.name === c.from)!.netId,
  )
  const reference = model.multilayer.copper.filter((c) => c.layer === "bottom")
  if (
    reference.length !== 1 ||
    new Set(signal_net_ids).size !== 2 ||
    signal_net_ids.includes(reference[0].netId)
  )
    throw new Error(
      "Exporter copper ownership does not match the selected independent signals/reference",
    )
  return {
    model,
    requirements,
    signal_net_ids,
    reference_net_id: reference[0].netId,
  }
}

export async function exportBoard(directory: string) {
  const circuitJson = await Bun.file(`${directory}/circuit.json`).json()
  const prepared: PreparedModel = await Bun.file(
    `${directory}/model.json`,
  ).json()
  const geometry = boardGeometry(circuitJson, prepared)
  await Bun.write(
    `${directory}/exporter-input.json`,
    JSON.stringify(geometry, null, 2) + "\n",
  )
  const result = await exportGmsh({
    model: geometry.model,
    outputDirectory: `${directory}/board`,
    python: process.env.PALACE_PYTHON ?? process.env.GMSH_PYTHON,
    meshSizeMm: prepared.setup.mesh.far_mm,
    conformal: true,
    threads: 1,
    repairRadiusMm: 0,
    simplifyToleranceMm: 0,
    validationRequirements: geometry.requirements,
  })
  if (
    !result.validation?.passed ||
    !result.validation.pcbChecksComplete ||
    result.report.repairs.length ||
    result.report.removedVolumeMm3 > 1e-9
  )
    throw new Error(
      "Exporter geometry or complete connectivity validation failed",
    )
  console.log(
    JSON.stringify({
      stage: "board_exported",
      mesh: result.meshPath,
      tetrahedra: result.report.tetrahedra,
    }),
  )
}

if (import.meta.main) await exportBoard(resolve(Bun.argv[2]))
