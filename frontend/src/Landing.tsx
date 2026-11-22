// The opening band: what NORWICH//SIM is, the city model it runs (real
// figures from GET /city/overview: the road network, the census, the
// mapped stations), and a way into each part. It can be folded away; the
// choice is remembered.

import { useState } from "react"

import { Icon } from "./components.tsx"
import type { Overview } from "./overview.ts"

const FOLDED_KEY = "norwich-sim-intro-folded"

function folded(): boolean {
  try {
    return localStorage.getItem(FOLDED_KEY) === "1"
  } catch {
    return false
  }
}

const number = (n: number) => n.toLocaleString("en-GB")

export function Landing({
  overview,
  onOpen,
  onAsk,
}: {
  overview: Overview | null
  onOpen: (view: "command" | "plan" | "3d") => void
  onAsk?: () => void
}) {
  const [hidden, setHidden] = useState(folded)
  const fold = (value: boolean) => {
    setHidden(value)
    try {
      localStorage.setItem(FOLDED_KEY, value ? "1" : "0")
    } catch {
      // Private browsing: the choice lasts for this visit
    }
  }

  if (hidden) {
    return (
      <div className="landing landing--folded">
        <button type="button" className="landing-unfold" onClick={() => fold(false)}>
          <Icon name="info" size={14} /> About this digital twin
        </button>
      </div>
    )
  }

  const stations = overview?.osm_stations
  const facts: { label: string; value: string; note: string }[] = overview
    ? [
        { label: "Road network", value: `${overview.road_km.toLocaleString("en-GB")} km`, note: `${number(overview.road_segments)} roads, ${number(overview.junctions)} junctions` },
        ...(overview.population ? [{ label: "Residents", value: number(overview.population.residents), note: `${overview.population.source}, ${overview.population.output_areas} areas` }] : []),
        ...(stations ? [{ label: "Stations mapped", value: String(stations.fire + stations.police + stations.ambulance), note: `${stations.fire} fire, ${stations.police} police, ${stations.ambulance} ambulance` }] : []),
        ...(overview.signalised_junctions !== null ? [{ label: "Traffic signals", value: number(overview.signalised_junctions), note: "junctions from OpenStreetMap" }] : []),
      ]
    : []

  return (
    <section className="landing" aria-label="About NORWICH//SIM">
      <div className="landing-copy">
        <span className="landing-eyebrow">Norwich, UK · Live digital twin</span>
        <h2>Norwich's roads, traffic and emergency cover, simulated live.</h2>
        <p>
          Every junction and road from OpenStreetMap, every resident from the 2021 Census. Run traffic, close
          bridges, flood the Wensum, dispatch emergency units and plan where stations should go. Every figure
          comes from the model.
        </p>
        <div className="landing-actions">
          <button type="button" className="landing-primary" onClick={() => onOpen("command")}>
            <Icon name="siren" size={15} /> Command centre
          </button>
          <button type="button" onClick={() => onOpen("3d")}>
            <Icon name="route" size={15} /> 3D traffic
          </button>
          <button type="button" onClick={() => onOpen("plan")}>
            <Icon name="stations" size={15} /> Station planning
          </button>
          {onAsk && (
            <button type="button" onClick={onAsk}>
              <Icon name="chat" size={15} /> Ask the analyst
            </button>
          )}
        </div>
      </div>
      <dl className="landing-facts" aria-label="The city model">
        {facts.length === 0 ? (
          <div className="landing-fact landing-fact--loading"><dt>City model</dt><dd>Loading…</dd></div>
        ) : facts.map((fact) => (
          <div key={fact.label} className="landing-fact">
            <dt>{fact.label}</dt>
            <dd>{fact.value}</dd>
            <small>{fact.note}</small>
          </div>
        ))}
      </dl>
      <button type="button" className="landing-fold" aria-label="Fold the introduction away" onClick={() => fold(true)}>
        <Icon name="close" size={14} />
      </button>
    </section>
  )
}

// The topbar's status: the backend's live runs and whether the analyst
// has an AI model behind it
export function StatusPill({ overview }: { overview: Overview | null }) {
  if (!overview) {
    return <span className="status-pill status-pill--off" role="status">Connecting…</span>
  }
  return (
    <span className="status-pill" role="status" title={overview.ai.available ? `AI analyst: ${overview.ai.model}` : `AI analyst: built-in rules (${overview.ai.reason})`}>
      <i aria-hidden="true" />
      Model online · {overview.live_runs} live run{overview.live_runs === 1 ? "" : "s"}
    </span>
  )
}
