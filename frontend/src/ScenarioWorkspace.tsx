// Scenario analysis: build scenarios, run them as a study in the
// background, and compare the results (ranking, charts, why, and the
// roads that lost the most time, which can be drawn on the map).
//
// Requests and the plain logic live in scenarios.ts.

import { useEffect, useRef, useState } from "react"

import { Icon, KpiCard, Spinner } from "./components.tsx"
import { BarChart } from "./OptimiserResults.tsx"
import {
  STUDY_POLL_MS,
  delaySeries,
  formatSeconds,
  formatValue,
  histogram,
  isFinished,
  linePoints,
  postJson,
  pickedRoadLabel,
  progressLabel,
  randomClosureRequest,
  request,
  roadLabel,
  studyRequest,
  METRIC_OPTIONS,
  type Draft,
  type NearestRoad,
  type Road,
  type RoadImpact,
  type ScenarioDetail,
  type Study,
} from "./scenarios.ts"

// What the map draws for a scenario (see App.tsx)
export type ScenarioOverlay = {
  title: string
  closures: { start: [number, number]; end: [number, number] }[]
  hotspots: RoadImpact[]
}

const VEHICLES = [200, 500, 1000, 2000]

// Shown for every scenario in the ranking table, besides the ranked metric
const TABLE_COLUMNS = [
  { key: "total_delay_vehicle_hours", label: "Network delay", unit: "vehicle-hours" },
  { key: "mean_increase_seconds", label: "Mean increase", unit: "s" },
  { key: "p95_increase_seconds", label: "95th percentile", unit: "s" },
  { key: "completion_percent", label: "Completed", unit: "%" },
]
const CHART_COLOURS = ["#2a5d8f", "#a86d17", "#6a55a3", "#2f7d6b", "#b4423a", "#173e60"]
const SHOWN_ROWS = 15

let nextKey = 1

function newDraft(name: string, overrides: Partial<Draft> = {}): Draft {
  return {
    key: nextKey++,
    name,
    vehicles: 500,
    seed: 1,
    rerouting: true,
    surge: false,
    closures: [],
    accidents: [],
    ...overrides,
  }
}

type PickTarget = { key: number; kind: "closure" | "accident" }

export function ScenarioWorkspace({
  onPickRoad,
  onShowOnMap,
  openStudy = null,
}: {
  // Ask the map for one click (label says what for); null cancels
  onPickRoad: (label: string | null, handler: ((latitude: number, longitude: number) => void) | null) => void
  onShowOnMap: (overlay: ScenarioOverlay | null) => void
  // A saved study to show (from the History section); the nonce lets the
  // same study be opened again
  openStudy?: { id: string; nonce: number } | null
}) {
  const [drafts, setDrafts] = useState<Draft[]>(() => [
    newDraft("Baseline"),
    newDraft("Road closure"),
  ])
  const [baselineKey, setBaselineKey] = useState<number | null>(() => drafts[0].key)
  const [studyName, setStudyName] = useState("My study")
  const [metric, setMetric] = useState(METRIC_OPTIONS[0].key)
  const [picking, setPicking] = useState<PickTarget | null>(null)

  const [sweepCount, setSweepCount] = useState(20)
  const [sweepClosures, setSweepClosures] = useState(3)
  const [sweepRadius, setSweepRadius] = useState(1500)

  const [study, setStudy] = useState<Study | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [details, setDetails] = useState<Record<string, ScenarioDetail>>({})
  const [selected, setSelected] = useState<string | null>(null)

  const poll = useRef<number | null>(null)

  // Stop checking the study if the page goes away
  useEffect(() => () => stopPolling(), [])

  // Show a saved study chosen in the History section: clear what is
  // shown now (while rendering, as React advises for state that follows a
  // prop), then start checking the study
  const [shownRequest, setShownRequest] = useState(openStudy)
  if (openStudy !== shownRequest) {
    setShownRequest(openStudy)
    if (openStudy) {
      setError(null)
      setStudy(null)
      setDetails({})
      setSelected(null)
    }
  }

  useEffect(() => {
    if (!openStudy) return
    onShowOnMap(null)
    follow(openStudy.id)
    document.getElementById("scenarios")?.scrollIntoView?.({ behavior: "smooth" })
    // follow and onShowOnMap are recreated each render; only a new
    // request to open a study should run this
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openStudy])

  // ---------------- building ----------------

  const update = (key: number, change: Partial<Draft>) =>
    setDrafts((current) => current.map((d) => (d.key === key ? { ...d, ...change } : d)))

  const startPick = (key: number, kind: PickTarget["kind"]) => {
    const draft = drafts.find((d) => d.key === key)
    if (!draft) return

    setPicking({ key, kind })
    onPickRoad(
      `${kind === "closure" ? "Road to close" : "Accident road"} for ${draft.name}`,
      async (latitude, longitude) => {
        setPicking(null)
        try {
          const road = await request<NearestRoad>(
            `/scenarios/nearest-road?latitude=${latitude}&longitude=${longitude}`
          )
          const label = pickedRoadLabel(road)
          // The same road (by its junctions) is only listed once; two
          // roads can share a name
          const other = (r: Road) =>
            r.start !== road.start || r.destination !== road.destination
          setDrafts((current) =>
            current.map((d) => {
              if (d.key !== key) return d
              const item = { start: road.start, destination: road.destination, label }
              return kind === "closure"
                ? { ...d, closures: [...d.closures.filter(other), item] }
                : {
                    ...d,
                    accidents: [
                      ...d.accidents.filter(other),
                      { ...item, severity: "severe", start_at: 120 },
                    ],
                  }
            })
          )
          onShowOnMap({
            title: `${kind === "closure" ? "Closure" : "Accident"} picked`,
            closures: [{
              start: [road.start_point.latitude, road.start_point.longitude],
              end: [road.end_point.latitude, road.end_point.longitude],
            }],
            hotspots: [],
          })
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : "Could not find a road there")
        }
      }
    )
  }

  const cancelPick = () => {
    setPicking(null)
    onPickRoad(null, null)
  }

  const addDraft = () => {
    const draft = newDraft(`Scenario ${drafts.length + 1}`)
    setDrafts((current) => [...current, draft])
  }

  const removeDraft = (key: number) => {
    setDrafts((current) => current.filter((d) => d.key !== key))
    if (baselineKey === key) setBaselineKey(null)
  }

  // ---------------- running ----------------

  function stopPolling() {
    if (poll.current !== null) {
      window.clearInterval(poll.current)
      poll.current = null
    }
  }

  const follow = (studyId: string) => {
    stopPolling()

    const check = async () => {
      try {
        const latest = await request<Study>(`/scenarios/studies/${studyId}?metric=${metric}`)
        setStudy(latest)
        if (isFinished(latest)) {
          stopPolling()
          loadCharts(latest)
        }
      } catch (caught) {
        stopPolling()
        setError(caught instanceof Error ? caught.message : "Lost contact with the study")
      }
    }

    poll.current = window.setInterval(check, STUDY_POLL_MS)
    void check()
  }

  const submit = async (body: object, path: string) => {
    setSubmitting(true)
    setError(null)
    setStudy(null)
    setDetails({})
    setSelected(null)
    onShowOnMap(null)

    try {
      const created = await postJson<{ study_id: string }>(path, body)
      follow(created.study_id)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The study could not be started")
    } finally {
      setSubmitting(false)
    }
  }

  const runStudy = () =>
    submit(studyRequest(studyName, drafts, baselineKey, metric), "/scenarios/studies")

  const runSweep = () => {
    const base = drafts.find((d) => d.key === baselineKey) ?? drafts[0]
    return submit(
      randomClosureRequest(base, sweepCount, sweepClosures, sweepRadius, metric),
      "/scenarios/studies/sweep"
    )
  }

  const cancel = async () => {
    if (!study) return
    try {
      await postJson(`/scenarios/studies/${study.study_id}/cancel`, {})
    } catch {
      // It may have finished meanwhile; the next check shows its state
    }
  }

  // ---------------- results ----------------

  const loadDetail = async (scenarioId: string) => {
    if (details[scenarioId]) return details[scenarioId]
    const detail = await request<ScenarioDetail>(`/scenarios/${scenarioId}`)
    setDetails((current) => ({ ...current, [scenarioId]: detail }))
    return detail
  }

  // The delay-over-time chart needs each scenario's time series
  function loadCharts(latest: Study) {
    const done = latest.scenarios.filter((s) => s.status === "completed").slice(0, CHART_COLOURS.length)
    for (const scenario of done) {
      void loadDetail(scenario.scenario_id).catch(() => {})
    }
  }

  const select = async (scenarioId: string) => {
    setSelected(scenarioId)
    try {
      const detail = await loadDetail(scenarioId)
      onShowOnMap({
        title: detail.name,
        closures: detail.disruptions.map((road) => ({
          start: [road.start.latitude, road.start.longitude],
          end: [road.end.latitude, road.end.longitude],
        })),
        hotspots: detail.result?.roads.slice(0, 10) ?? [],
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load the scenario")
    }
  }

  const running = study !== null && !isFinished(study)
  const selectedDetail = selected ? details[selected] : undefined

  return (
    <section className="scenarios" id="scenarios" aria-labelledby="scenarios-title">
      <div className="scenarios-head">
        <div>
          <h2 id="scenarios-title">Scenario analysis</h2>
          <p>
            Run full simulations of different situations on the Norwich network and
            compare their effect on the whole network.
          </p>
        </div>
      </div>

      <div className="scenarios-body">
        {/* ---------------- BUILDER ---------------- */}
        <div className="card scenario-builder" aria-label="Scenario builder" role="region">
          <label className="field">
            Study name
            <input value={studyName} onChange={(e) => setStudyName(e.target.value)} />
          </label>

          <ol className="drafts">
            {drafts.map((draft) => (
              <li key={draft.key} className="draft" aria-label={`Scenario ${draft.name}`}>
                <div className="draft-head">
                  <input
                    className="draft-name"
                    aria-label="Scenario name"
                    value={draft.name}
                    onChange={(e) => update(draft.key, { name: e.target.value })}
                  />
                  <label className="check">
                    <input
                      type="radio"
                      name="baseline"
                      checked={baselineKey === draft.key}
                      onChange={() => setBaselineKey(draft.key)}
                    />
                    Baseline
                  </label>
                  {drafts.length > 1 && (
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`Remove ${draft.name}`}
                      onClick={() => removeDraft(draft.key)}
                    >
                      <Icon name="close" size={14} />
                    </button>
                  )}
                </div>

                <div className="fields">
                  <label className="field">
                    Vehicles
                    <select
                      value={draft.vehicles}
                      onChange={(e) => update(draft.key, { vehicles: Number(e.target.value) })}
                    >
                      {VEHICLES.map((n) => (
                        <option key={n} value={n}>{n.toLocaleString("en-GB")}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    Seed
                    <select
                      value={draft.seed}
                      onChange={(e) => update(draft.key, { seed: Number(e.target.value) })}
                    >
                      {[1, 2, 3, 4, 5].map((n) => (
                        <option key={n} value={n}>{n}</option>
                      ))}
                    </select>
                  </label>
                </div>

                <div className="checks">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={draft.rerouting}
                      onChange={(e) => update(draft.key, { rerouting: e.target.checked })}
                    />
                    Dynamic rerouting
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={draft.surge}
                      onChange={(e) => update(draft.key, { surge: e.target.checked })}
                    />
                    City-centre surge
                  </label>
                </div>

                <div className="chips" aria-label={`Disruptions in ${draft.name}`}>
                  {draft.closures.map((road) => (
                    <span key={`c-${road.label}`} className="chip">
                      Closed {road.label}
                      <button
                        type="button"
                        aria-label={`Reopen ${road.label}`}
                        onClick={() => update(draft.key, { closures: draft.closures.filter((c) => c !== road) })}
                      >
                        <Icon name="close" size={12} />
                      </button>
                    </span>
                  ))}
                  {draft.accidents.map((road) => (
                    <span key={`a-${road.label}`} className="chip chip--warn">
                      Accident {road.label}
                      <button
                        type="button"
                        aria-label={`Clear accident ${road.label}`}
                        onClick={() => update(draft.key, { accidents: draft.accidents.filter((a) => a !== road) })}
                      >
                        <Icon name="close" size={12} />
                      </button>
                    </span>
                  ))}
                </div>

                <div className="draft-actions">
                  {picking?.key === draft.key ? (
                    <button type="button" className="btn btn--quiet" onClick={cancelPick}>
                      Click a road on the map… (cancel)
                    </button>
                  ) : (
                    <>
                      <button type="button" className="btn btn--quiet" onClick={() => startPick(draft.key, "closure")}>
                        Close a road
                      </button>
                      <button type="button" className="btn btn--quiet" onClick={() => startPick(draft.key, "accident")}>
                        Add an accident
                      </button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ol>

          <button type="button" className="btn" onClick={addDraft} disabled={drafts.length >= 8}>
            <Icon name="plus" size={16} />
            Add scenario
          </button>

          <label className="field">
            Rank by
            <select value={metric} onChange={(e) => setMetric(e.target.value)}>
              {METRIC_OPTIONS.map((m) => (
                <option key={m.key} value={m.key}>{m.label}</option>
              ))}
            </select>
          </label>

          <button
            type="button"
            className="btn btn--primary btn--block"
            onClick={runStudy}
            disabled={submitting || running}
            aria-busy={submitting}
          >
            {submitting ? <Spinner /> : <Icon name="route" size={16} />}
            Run {drafts.length} scenario{drafts.length === 1 ? "" : "s"}
          </button>

          <details className="sweep">
            <summary>Large study: random closures</summary>
            <p>
              The baseline scenario, then many scenarios that each close roads drawn at
              random near the city centre (Monte Carlo): how the network copes with
              disruption in general.
            </p>
            <div className="fields">
              <label className="field">
                Scenarios
                <select value={sweepCount} onChange={(e) => setSweepCount(Number(e.target.value))}>
                  {[20, 100, 400].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <label className="field">
                Closures each
                <select value={sweepClosures} onChange={(e) => setSweepClosures(Number(e.target.value))}>
                  {[1, 3, 5].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <label className="field">
                Within
                <select value={sweepRadius} onChange={(e) => setSweepRadius(Number(e.target.value))}>
                  <option value={1000}>1 km</option>
                  <option value={1500}>1.5 km</option>
                  <option value={2500}>2.5 km</option>
                </select>
              </label>
            </div>
            <button type="button" className="btn btn--block" onClick={runSweep} disabled={submitting || running}>
              Run random-closure study
            </button>
          </details>
        </div>

        {/* ---------------- RESULTS ---------------- */}
        <div className="scenario-results" aria-label="Scenario results" role="region">
          {error && (
            <div className="notice notice--error" role="alert">
              <Icon name="alert" size={16} />
              <span>{error}</span>
            </div>
          )}

          {!study && !error && (
            <div className="empty">
              <strong>No scenario results yet</strong>
              <span>
                Set up two or more scenarios (pick closures and accidents on the map) and run
                them. Each is a full simulation; the results are ranked and explained here.
              </span>
            </div>
          )}

          {study && (
            <StudyResults
              study={study}
              details={details}
              running={running}
              onCancel={cancel}
              onSelect={select}
              selected={selected}
            />
          )}

          {selectedDetail && <ScenarioDetailCard detail={selectedDetail} />}
        </div>
      </div>
    </section>
  )
}

// ------------------------------------------------
// A study: progress, ranking, charts, explanation
// ------------------------------------------------

function StudyResults({
  study,
  details,
  running,
  onCancel,
  onSelect,
  selected,
}: {
  study: Study
  details: Record<string, ScenarioDetail>
  running: boolean
  onCancel: () => void
  onSelect: (id: string) => void
  selected: string | null
}) {
  // How the backend ranked it (its own definition of the metric)
  const definition = study.ranking_metric
  const ranking = study.ranking ?? []

  // The table's other columns, leaving out the one ranked by
  const columns = TABLE_COLUMNS.filter((column) => column.key !== definition.key)
  const shown = ranking.slice(0, SHOWN_ROWS)
  const unit = study.ranking_metric.unit
  const badge =
    study.status === "completed" ? "badge badge--ok"
      : study.status === "running" || study.status === "queued" ? "badge"
        : "badge badge--warn"

  const series = study.scenarios
    .filter((s) => details[s.scenario_id]?.result)
    .slice(0, CHART_COLOURS.length)
    .map((s, index) => ({
      name: s.name,
      colour: CHART_COLOURS[index],
      points: delaySeries(details[s.scenario_id].result!.timeseries),
    }))

  const delays = ranking
    .map((row) => row.metrics.total_delay_vehicle_hours)
    .filter((v): v is number => v !== null)

  return (
    <div className="card results-card" aria-label="Study results" role="region">
      <div className="study-head">
        <h3 className="results-title">{study.name}</h3>
        <span className={badge}>{study.status}</span>
      </div>

      <div className="progress" aria-label="Study progress">
        <div
          className="progress-bar"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(study.progress_percent)}
        >
          <span style={{ width: `${study.progress_percent}%` }} />
        </div>
        <span className="progress-text">
          {progressLabel(study)}
          {study.wall_seconds !== null && ` in ${formatSeconds(study.wall_seconds)}`}
          {` · ${study.workers} worker${study.workers === 1 ? "" : "s"}`}
        </span>
        {running && (
          <button type="button" className="btn btn--quiet" onClick={onCancel}>
            Cancel study
          </button>
        )}
      </div>

      {study.explanation.length > 0 && (
        <div className="results-summary" aria-label="Why this ranking">
          <strong>Why this ranking</strong>
          <ul>
            {study.explanation.map((sentence) => <li key={sentence}>{sentence}</li>)}
          </ul>
        </div>
      )}

      {ranking.length > 0 && (
        <>
          <div className="chart-grid">
            <BarChart
              title={definition.label}
              bars={shown.slice(0, 10).map((row) => ({
                label: row.name,
                value: row.value,
                text: formatValue(row.value, unit),
                best: row.rank === 1,
              }))}
              note={definition.better === "lower" ? "Shorter is better." : "Longer is better."}
            />
            {series.length > 0 && <DelayChart series={series} />}
            {delays.length > 10 && <Histogram values={delays} />}
          </div>

          <div className="table-wrap">
            <table className="data-table" aria-label="Scenario ranking">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Scenario</th>
                  <th>{definition.label}</th>
                  {columns.map((column) => <th key={column.key}>{column.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => (
                  <tr key={row.id} className={row.rank === 1 ? "is-highlight" : undefined}>
                    <td>{row.rank}</td>
                    <td>
                      {/* The name opens the scenario's details */}
                      <button
                        type="button"
                        className="link-btn"
                        aria-label={`Show details of ${row.name}`}
                        aria-pressed={selected === row.id}
                        onClick={() => onSelect(row.id)}
                      >
                        {row.name}
                      </button>
                    </td>
                    <td>{formatValue(row.value, unit)}</td>
                    {columns.map((column) => (
                      <td key={column.key}>{formatValue(row.metrics[column.key], column.unit)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {ranking.length > SHOWN_ROWS && (
            <p className="method-note">
              Showing the best {SHOWN_ROWS} of {ranking.length} completed scenarios.
            </p>
          )}
        </>
      )}

      {study.scenarios.some((s) => s.status === "failed") && (
        <ul className="failures" aria-label="Failed scenarios">
          {study.scenarios.filter((s) => s.status === "failed").map((s) => (
            <li key={s.scenario_id}>{s.name}: {s.error}</li>
          ))}
        </ul>
      )}

      <div className="method-note">
        Each scenario is a full simulation of every trip on the Norwich road network.
        Network delay is the time trips lost to traffic, accidents and closures;
        journey-time increase is the extra time over each trip's free-flow fastest
        route, so it also counts detours. {study.ranking_metric.definition}
      </div>
    </div>
  )
}

// Delay per simulated minute for several scenarios (an SVG line chart,
// with the numbers written out for screen readers)
function DelayChart({
  series,
}: {
  series: { name: string; colour: string; points: { x: number; y: number }[] }[]
}) {
  const width = 320
  const height = 140
  const maxX = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.x)))
  const maxY = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.y)))

  return (
    <div className="chart">
      <h3>Delay over time</h3>
      <svg
        className="line-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Delay per simulated minute, up to ${Math.round(maxY)} vehicle-minutes`}
      >
        <line x1="0" y1={height} x2={width} y2={height} className="axis" />
        {series.map((s) => (
          <polyline
            key={s.name}
            points={linePoints(s.points, width, height, maxX, maxY)}
            fill="none"
            stroke={s.colour}
            strokeWidth="2"
          />
        ))}
      </svg>
      <ul className="legend-inline">
        {series.map((s) => (
          <li key={s.name}>
            <span className="swatch" style={{ background: s.colour }} />
            {s.name}: peak {Math.round(Math.max(0, ...s.points.map((p) => p.y)))} vehicle-minutes
            lost in a minute
          </li>
        ))}
      </ul>
      <p className="chart-note">Vehicle-minutes lost per simulated minute, over {Math.round(maxX)} minutes.</p>
    </div>
  )
}

// How network delay spread across many scenarios (a large study)
function Histogram({ values }: { values: number[] }) {
  const bins = histogram(values, 8)

  return (
    <BarChart
      title="Spread of network delay"
      bars={bins.map((bin) => ({
        label: `${bin.from.toFixed(1)}-${bin.to.toFixed(1)}`,
        value: bin.count,
        text: `${bin.count} scenario${bin.count === 1 ? "" : "s"}`,
      }))}
      note="Vehicle-hours lost, across all completed scenarios."
    />
  )
}

// ------------------------------------------------
// One scenario in detail
// ------------------------------------------------

function ScenarioDetailCard({ detail }: { detail: ScenarioDetail }) {
  const result = detail.result
  if (!result) return null

  const metrics = result.metrics
  const versus = detail.vs_baseline?.paired

  return (
    <div className="card results-card" aria-label={`Details of ${detail.name}`} role="region">
      <h3 className="results-title">{detail.name}</h3>

      <div className="kpi-row">
        <KpiCard label="Network delay" value={formatValue(metrics.total_delay_vehicle_hours, "vehicle-hours")} />
        <KpiCard label="Mean increase" value={formatValue(metrics.mean_increase_seconds, "s")} />
        <KpiCard label="Worst increase" value={formatValue(metrics.max_increase_seconds, "s")} />
        <KpiCard
          label="Trips completed"
          value={formatValue(metrics.completion_percent, "%")}
          sub={`${result.trip_counts.unroutable ?? 0} cut off`}
        />
      </div>

      {versus && detail.vs_baseline && (
        <div className="results-summary" aria-label="Against the baseline">
          <strong>Against {detail.vs_baseline.baseline_name}, trip by trip</strong>
          <ul>
            <li>
              {versus.trips_slower} of {versus.trips_compared} trips took longer, {versus.trips_faster} less
              time; {versus.extra_vehicle_hours >= 0 ? "+" : ""}
              {versus.extra_vehicle_hours.toFixed(1)} vehicle-hours in total.
            </li>
            {versus.worst_increase_seconds !== null && versus.worst_increase_seconds > 0 && (
              <li>Worst single trip: {formatSeconds(versus.worst_increase_seconds)} longer.</li>
            )}
            {versus.newly_cut_off > 0 && <li>{versus.newly_cut_off} trips had no route at all.</li>}
          </ul>
        </div>
      )}

      <div className="table-wrap">
        <table className="data-table" aria-label="Roads that lost the most time">
          <thead>
            <tr>
              <th>#</th>
              <th>Road (junctions)</th>
              <th>Time lost</th>
              <th>Share of time there</th>
              <th>Most vehicles at once</th>
            </tr>
          </thead>
          <tbody>
            {result.roads.slice(0, 10).map((road) => (
              <tr key={road.rank}>
                <td>{road.rank}</td>
                <td>{roadLabel(road)}</td>
                <td>{formatSeconds(road.delay_vehicle_seconds)}</td>
                <td>{Math.round(road.delay_share * 100)}%</td>
                <td>{road.peak_vehicles}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="method-note">
        The ten roads that lost the most time are drawn on the map in amber.
        Time lost is summed over every vehicle on the road.
      </p>
    </div>
  )
}
