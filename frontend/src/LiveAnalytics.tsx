// Live charts of the running simulation, drawn from the summary every
// step returns (no extra requests): vehicles driving and held up, and
// the average speed as a share of free flow, and (cellular-automaton runs
// only) the share of cars standing still in queues.

import { KpiCard } from "./components.tsx"
import { describeSample, heldUpPercent, series, speedPercent, stoppedPercent, type Sample } from "./live.ts"
import { linePoints } from "./scenarios.ts"

const WIDTH = 520
const HEIGHT = 120

function LineChart({
  title,
  points,
  maxY,
  unit,
}: {
  title: string
  points: { x: number; y: number }[]
  maxY: number
  unit: string
}) {
  const maxX = points.length ? Math.max(points[points.length - 1].x, 60) : 60
  const last = points[points.length - 1]
  return (
    <figure className="live-chart">
      <figcaption>
        {title}
        {last && <strong> {Math.round(last.y)}{unit}</strong>}
      </figcaption>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
        aria-label={`${title} over ${Math.round(maxX)} simulated seconds`}>
        <line x1="0" y1={HEIGHT} x2={WIDTH} y2={HEIGHT} className="axis" />
        <polyline points={linePoints(points, WIDTH, HEIGHT, maxX, maxY)} className="series" />
      </svg>
    </figure>
  )
}

export function LiveAnalytics({ history }: { history: Sample[] }) {
  const latest = history[history.length - 1]
  const active = series(history, (s) => s.active_vehicles)
  const peak = Math.max(10, ...active.map((p) => p.y))
  const stopped = series(history, stoppedPercent)

  return (
    <section className="card live-analytics" id="analytics" aria-labelledby="analytics-title">
      <h2 id="analytics-title" className="results-title">Live traffic</h2>
      <p className="hint" role="status">{describeSample(latest)}</p>
      {latest && (
        <>
          <div className="kpis kpis--compact">
            <KpiCard label="Arrived" value={latest.arrived.toLocaleString("en-GB")} />
            <KpiCard
              label="Mean journey"
              value={latest.mean_journey_seconds === null ? "-" : `${Math.round(latest.mean_journey_seconds)} s`}
            />
            <KpiCard label="Route changes" value={latest.reroutes.toLocaleString("en-GB")} />
            <KpiCard label="Incidents" value={`${latest.accidents + latest.closed_roads}`} />
          </div>
          <div className="live-charts">
            <LineChart title="Vehicles driving" points={active} maxY={peak} unit="" />
            <LineChart title="Held up by traffic" points={series(history, heldUpPercent)} maxY={100} unit="%" />
            <LineChart title="Average speed (of free flow)" points={series(history, speedPercent)} maxY={100} unit="%" />
            {stopped.length > 0 && (
              <LineChart title="Stopped in queues" points={stopped} maxY={100} unit="%" />
            )}
          </div>
        </>
      )}
    </section>
  )
}
