import { expect, test } from "bun:test"
import { renderCoupledRoutes } from "../examples/coupled-routes"
import { exampleSetup } from "../examples/setup"
import { boardGeometry } from "../lib/export"
import { prepare } from "../lib/prepare"

test("real exporter API preserves explicit zero loss and physical copper ownership", async () => {
  const cj = await renderCoupledRoutes(0.1)
  const original = JSON.stringify(cj)
  const ready = prepare(cj, exampleSetup(cj))
  if (ready.status !== "ready") throw new Error(ready.issues.join(";"))
  const geometry = boardGeometry(cj, ready.model)
  const physical = geometry.model.multilayer.stackup
  expect(physical.dielectrics[0].lossTangent).toBe(0)
  expect(physical.dielectrics[0].dielectricConstant).toBe(4)
  expect(physical.copperLayers.map((x) => [x.name, x.zMin, x.zMax])).toEqual([
    ["top", 0.2, 0.23500000000000001],
    ["bottom", -0.035, 0],
  ])
  expect(new Set(geometry.signal_net_ids).size).toBe(2)
  expect(geometry.signal_net_ids).not.toContain(geometry.reference_net_id)
  expect(geometry.requirements.connections).toHaveLength(2)
  expect(JSON.stringify(cj)).toBe(original)
  const dielectric = ready.model.stackup.layers[1]
  if (dielectric.type !== "dielectric") throw new Error("Unexpected stack")
  delete dielectric.dielectric_loss_tangent
  expect(() => boardGeometry(cj, ready.model)).toThrow("Explicit Er and loss")
})
