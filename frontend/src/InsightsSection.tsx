// City intelligence: the backend's calculated analyses in one place
// (GET /intelligence/insights): where fire and police response is slowest,
// the one extra station that would help most, the roads trips depend on,
// and for the live run its findings, bottlenecks and congestion anomalies.
// Asked for only when the visitor runs it (it takes a few seconds).

import { useState } from "react"

import { activeRun } from "./activeRun.ts"
import { API_URL } from "./config.ts"
import { STAGE_TEXT, askStream, type Answer, type Stage, type Status } from "./intelligence.ts"

const EXPLAIN_QUESTION =
  "Explain why fire and police coverage is weakest where it is, which roads Norwich depends on most, " +
  "and the trade-offs of adding one new fire station."

type Area = { where: string; residents_beyond_target: number; median_seconds: number | null }
type Coverage = {
  layout: string
  stations: string[]
  summary: { population_within_target_percent: number; population_beyond_target: number }
  worst_areas: Area[]
  method: string
}
type Station = {
  before: { population_within_target_percent: number } | null
  candidates_tried: number
  best: { where: string; population_within_target_percent: number; residents_brought_within_target?: number }[]
}
type Road = { road: string; where: string; trips_using_it_percent: number; mean_extra_seconds_if_closed: number; trips_cut_off_if_closed: number }
type Bottleneck = { road: string; where: string; vehicles: number; speed_percent: number }
type Anomaly = { road?: string; where?: string; vehicles: number; usual: number; since: number }

export type Insights = {
  coverage: Record<"fire" | "police", Coverage>
  stations: Record<"fire" | "police", Station>
  critical_roads: { trips_sampled: number; method: string; roads: Road[] }
  run: null | {
    simulation_id: string
    report: { findings: string[]; figures: Record<string, number | string | null> }
    bottlenecks: { bottlenecks: Bottleneck[]; anomalies: Anomaly[]; simulation_time: number }
  }
}

const minutes = (s: number | null) => (s === null ? "-" : `${(s / 60).toFixed(1)} min`)
const LABEL = { fire: "Fire", police: "Police" } as const

export function InsightsSection() {
  const [data, setData] = useState<Insights | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [ai, setAi] = useState<Status | null>(null)
  const [explained, setExplained] = useState<Answer | null>(null)
  const [explaining, setExplaining] = useState(false)
  const [stage, setStage] = useState<Stage | null>(null)

  async function explain() {
    setExplaining(true)
    try {
      setStage("planning")
      setExplained(await askStream(EXPLAIN_QUESTION, activeRun(), [], (event) => setStage(event.stage)))
    } catch (caught) {
      setExplained({ answer: "", mode: "rules", model: null, sources: [], notice: caught instanceof Error ? caught.message : String(caught) })
    } finally {
      setExplaining(false)
      setStage(null)
    }
  }

  async function load() {
    setBusy(true)
    setError("")
    try {
      const run = activeRun()
      const response = await fetch(`${API_URL}/intelligence/insights${run ? `?simulation_id=${encodeURIComponent(run)}` : ""}`)
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "The analysis failed")
      setData(body as Insights)
      // Whether a real AI model can explain the findings here
      fetch(`${API_URL}/intelligence/status`).then((r) => (r.ok ? r.json() : null)).then((s) => setAi(s)).catch(() => setAi(null))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="evaluation insights" id="insights" aria-labelledby="insights-title">
      <div className="scenarios-head">
        <div>
          <h2 id="insights-title">City intelligence</h2>
          <p>
            Where emergency response is weakest, where one more station would help most, which roads Norwich's trips
            depend on, and what is slowing the live simulation. Every figure is calculated by the model; nothing is
            estimated by AI.
          </p>
        </div>
        <button type="button" className="btn btn--primary" onClick={() => void load()} disabled={busy} aria-busy={busy}>
          {busy ? "Analysing…" : data ? "Analyse again" : "Run the analysis"}
        </button>
      </div>
      {error && <p className="analyst-error" role="alert">{error}</p>}
      {data && (
        <div className="insight-ai" aria-label="AI explanation of the findings">
          {ai?.available ? (
            <>
              <button type="button" className="btn" onClick={() => void explain()} disabled={explaining} aria-busy={explaining}>
                {explaining
                  ? `${stage && stage !== "done" && stage !== "calculated" ? STAGE_TEXT[stage] : "Running the calculations"}…`
                  : `Explain these findings with ${ai.model}`}
              </button>
              {explained && (
                <div className="insight-ai-answer">
                  {explained.explanation ? (
                    <><span className="analyst-badge analyst-badge--ai">AI-generated · {explained.model}</span><p>{explained.explanation}</p></>
                  ) : (
                    <p className="analyst-notice">{explained.explanation_note ?? explained.notice ?? "No explanation was given."}</p>
                  )}
                </div>
              )}
            </>
          ) : (
            <p className="insight-method">
              No AI model is available on this server{ai?.reason ? ` (${ai.reason})` : ""}, so these findings are not
              explained by AI. Run NORWICH//SIM locally with Ollama for AI explanations (docs/AI.md).
            </p>
          )}
        </div>
      )}

      {data && (
        <div className="insights-grid">
          {(["fire", "police"] as const).map((service) => {
            const coverage = data.coverage[service]
            const station = data.stations[service]
            const best = station.best[0]
            return (
              <article key={service} className="insight-card" aria-label={`${LABEL[service]} coverage`}>
                <span className="insight-tag">Calculated</span>
                <h3>{LABEL[service]}: response within 4 min</h3>
                <p className="insight-figure">{coverage.summary.population_within_target_percent.toFixed(1)}%
                  <small> of residents · {Math.round(coverage.summary.population_beyond_target).toLocaleString("en-GB")} beyond</small></p>
                <h4>Slowest areas</h4>
                <ul>
                  {coverage.worst_areas.map((area, i) => (
                    <li key={i}>{area.where}: {area.residents_beyond_target.toLocaleString("en-GB")} residents, median {minutes(area.median_seconds)}</li>
                  ))}
                </ul>
                {best && (
                  <p className="insight-recommend">
                    <b>Recommendation:</b> one more {service} station {best.where} would cover{" "}
                    {best.population_within_target_percent.toFixed(1)}%
                    {best.residents_brought_within_target !== undefined ? ` (+${best.residents_brought_within_target.toLocaleString("en-GB")} residents)` : ""},
                    the best of {station.candidates_tried} sites tried.
                  </p>
                )}
                <small className="insight-method">{coverage.method}; existing stations: {coverage.stations.join(", ") || "none"}.</small>
              </article>
            )
          })}

          <article className="insight-card" aria-label="Critical roads">
            <span className="insight-tag">Calculated</span>
            <h3>Roads Norwich's trips depend on</h3>
            <ol>
              {data.critical_roads.roads.map((road, i) => (
                <li key={i}>
                  <b>{road.road}</b> ({road.where}): {road.trips_using_it_percent}% of trips;
                  closed: +{Math.round(road.mean_extra_seconds_if_closed)} s each
                  {road.trips_cut_off_if_closed ? `, ${road.trips_cut_off_if_closed} cut off` : ""}
                </li>
              ))}
            </ol>
            <small className="insight-method">{data.critical_roads.method}.</small>
          </article>

          <article className="insight-card" aria-label="Live simulation">
            <span className="insight-tag">Calculated</span>
            <h3>The live simulation</h3>
            {data.run ? (
              <>
                {data.run.report.findings.map((finding, i) => <p key={i}>{finding}</p>)}
                {data.run.bottlenecks.anomalies.length > 0 ? (
                  <>
                    <h4>Unusually busy roads</h4>
                    <ul>
                      {data.run.bottlenecks.anomalies.map((a, i) => (
                        <li key={i}>{a.road ?? "a road"} ({a.where}): {a.vehicles} vehicles, usually {a.usual}</li>
                      ))}
                    </ul>
                  </>
                ) : <p className="insight-quiet">No congestion anomaly: no road is far above its own recent traffic.</p>}
                <small className="insight-method">Anomaly: at least 6 vehicles and 3 standard deviations above the road's own moving average.</small>
              </>
            ) : (
              <p className="insight-quiet">No live simulation is running. Plan a route, open the command centre or create a scenario with the analyst, then analyse again.</p>
            )}
          </article>
        </div>
      )}
    </section>
  )
}
