import { Circuit } from "@tscircuit/core"

/** Synthetic physical data, not a claim about any manufacturer's FR4. */
export const stackup = {
  source: "assumed" as const,
  layers: [
    {
      type: "copper" as const,
      layer: "top" as const,
      thickness_mm: 0.035,
      conductivity_s_per_m: 58e6,
    },
    {
      type: "dielectric" as const,
      material: "Synthetic nondispersive test dielectric",
      thickness_mm: 0.2,
      dielectric_constant: 4,
      dielectric_constant_frequency_hz: 1e9,
      dielectric_loss_tangent: 0,
      dielectric_loss_tangent_frequency_hz: 1e9,
    },
    {
      type: "copper" as const,
      layer: "bottom" as const,
      thickness_mm: 0.035,
      conductivity_s_per_m: 58e6,
    },
  ],
}

/** DDR-style parallel routes, with ideal fixture pads rather than IC models. */
export function CoupledRoutes({ gap = 0.1 }: { gap?: number }) {
  return (
    <board
      width={6}
      height={2.4}
      layers={2}
      thickness={0.27}
      routingDisabled
      schematicDisabled
      stackup={stackup}
    >
      <net name="GND" isGroundNet />
      {[0, 1].flatMap((line) =>
        [0, 1].map((end) => {
          const name = `U${1 + line + end * 2}`
          return (
            <chip
              key={name}
              name={name}
              pcbX={end ? 2 : -2}
              pcbY={((line ? 1 : -1) * (0.2 + gap)) / 2}
              pinLabels={{ pin1: "SIGNAL", pin2: "REF" }}
              footprint={
                <footprint>
                  <smtpad
                    portHints={["pin1"]}
                    shape="rect"
                    width={0.2}
                    height={0.2}
                    pcbX={0}
                    pcbY={0}
                    layer="top"
                  />
                  <smtpad
                    portHints={["pin2"]}
                    shape="rect"
                    width={0.2}
                    height={0.2}
                    pcbX={0}
                    pcbY={0}
                    layer="bottom"
                  />
                </footprint>
              }
            />
          )
        }),
      )}
      {[1, 2, 3, 4].map((n) => (
        <trace key={`ref${n}`} from={`.U${n} > .REF`} to="net.GND" />
      ))}
      {[0, 1].map((line) => (
        <trace
          key={`line${line}`}
          from={`.U${1 + line} > .SIGNAL`}
          to={`.U${3 + line} > .SIGNAL`}
          thickness={0.2}
          pcbPathRelativeTo={`.U${1 + line} > .SIGNAL`}
          pcbPath={[{ x: 4, y: 0 }]}
        />
      ))}
      <copperpour
        layer="bottom"
        connectsTo="net.GND"
        outline={[
          { x: -3, y: -1.2 },
          { x: 3, y: -1.2 },
          { x: 3, y: 1.2 },
          { x: -3, y: 1.2 },
        ]}
      />
    </board>
  )
}

export async function renderCoupledRoutes(gap = 0.1) {
  const circuit = new Circuit()
  circuit.add(<CoupledRoutes gap={gap} />)
  await circuit.renderUntilSettled()
  return circuit.getCircuitJson()
}
