// The emergency infrastructure planning workspace: a 3D map of response
// times across Norwich for existing and optimised station layouts, with a
// compact control panel, comparison figures, a legend and the optimisation
// played step by step. Everything shown comes from GET /optimiser/plan
// (backend optimiser/planning.py); see planning.ts.

import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react"

import type { Visible } from "./PlanningCanvas.tsx"
import {
  BANDS,
  CANDIDATE_COLOUR,
  DEFAULT_PLAN,
  HIGHLIGHT_COLOUR,
  METRES_PER_SECOND,
  PROPOSED_COLOUR,
  SERVICE_COLOURS,
  compare,
  differenceFeatures,
  existingStage,
  minutes,
  optimisationSteps,
  optimisedStage,
  planQuery,
  stationFeatures,
  type Disruption,
  type Plan,
  type PlanSettings,
  type ServiceName,
  type View,
} from "./planning.ts"

const PlanningCanvas = lazy(() => import("./PlanningCanvas.tsx"))

const STEP_MS = 2600

const SERVICE_LABELS: Record<ServiceName, string> = { ambulance: "Ambulance", fire: "Fire", police: "Police" }
const DISRUPTION_LABELS: Record<Disruption, string> = {
  none: "None",
  flood: "Wensum flood",
  bridges: "Bridges closed",
}

export function PlanningWorkspace({
  fetchPlan,
  onFailed,
}: {
  fetchPlan: (query: string) => Promise<Plan>
  onFailed: (reason: string) => void
}) {
  const [settings, setSettings] = useState<PlanSettings>(DEFAULT_PLAN)
  const [plan, setPlan] = useState<Plan | null>(null)
  // The settings the latest answer (or error) is for: loading until it
  // matches the current settings
  const [answered, setAnswered] = useState<{ query: string; error: string } | null>(null)
  const [view, setView] = useState<View>("optimised")
  const [step, setStep] = useState<number | null>(null)
  const [playing, setPlaying] = useState(false)
  const [visible, setVisible] = useState<Visible>({ coverage: true, underserved: true, stations: true, buildings: true })
  const [threeD, setThreeD] = useState(true)
  const query = planQuery(settings)
  const loading = answered?.query !== query
  const error = answered?.query === query ? answered.error : ""

  // Ask again whenever the settings change (only the latest answer counts)
  useEffect(() => {
    let current = true
    fetchPlan(query)
      .then((answer) => {
        if (!current) return
        setPlan(answer)
        setStep(null)
        setPlaying(false)
        // A plan with no existing layout cannot show it
        if (!existingStage(answer)) setView("optimised")
        setAnswered({ query, error: "" })
      })
      .catch((caught) => {
        if (current) setAnswered({ query, error: caught instanceof Error ? caught.message : "Could not plan" })
      })
    return () => {
      current = false
    }
  }, [query, fetchPlan])

  const steps = useMemo(() => (plan ? optimisationSteps(plan) : []), [plan])

  // Playing: one step every STEP_MS, stopping at the last
  const stepRef = useRef(step)
  useEffect(() => {
    stepRef.current = step
  })
  useEffect(() => {
    if (!playing) return
    const last = steps.length - 1
    const timer = setInterval(() => {
      const next = stepRef.current === null ? 0 : stepRef.current + 1
      if (next >= last) {
        setStep(last)
        setPlaying(false)
      } else {
        setStep(next)
      }
    }, STEP_MS)
    return () => clearInterval(timer)
  }, [playing, steps.length])

  const change = (update: Partial<PlanSettings>) => setSettings((current) => ({ ...current, ...update }))
  const current = step === null ? null : steps[step]
  const existing = plan ? existingStage(plan) : null

  // What the map shows: a step of the animation, or the chosen view
  const shown = useMemo(() => {
    if (!plan) return null
    if (current) {
      const stage = current.coverage === "existing" ? existing! : optimisedStage(plan, current.coverage)
      return { stage, fixed: null, stations: stationFeatures(plan, current, true) }
    }
    if (view === "difference") {
      return { stage: null, fixed: differenceFeatures(plan), stations: stationFeatures(plan, { candidates: "none", proposed: plan.optimised.length }, true) }
    }
    if (view === "existing" && existing) {
      return { stage: existing, fixed: null, stations: stationFeatures(plan, { candidates: "none", proposed: 0 }, true) }
    }
    return { stage: optimisedStage(plan), fixed: null, stations: stationFeatures(plan, { candidates: "none", proposed: plan.optimised.length }, true) }
  }, [plan, current, view, existing])

  const comparison = plan ? compare(plan) : []
  const target = plan?.target_seconds ?? settings.targetMinutes * 60

  return (
    <div className="planning" aria-label="Emergency infrastructure planning">
      <div className="planning-stage">
      <Suspense fallback={<div className="map map-loading">Loading the planning map…</div>}>
        <PlanningCanvas
          plan={plan}
          seconds={shown?.stage?.cells.seconds ?? null}
          beyond={shown?.stage?.cells.beyond_people ?? []}
          fixed={shown?.fixed ?? null}
          columns={shown?.stations.columns ?? { type: "FeatureCollection", features: [] }}
          labels={shown?.stations.labels ?? { type: "FeatureCollection", features: [] }}
          visible={visible}
          threeD={threeD}
          turn={step ?? 0}
          onFailed={onFailed}
        />
      </Suspense>

      {/* ---- The current step (top centre) ---- */}
      {current && (
        <section className="planning-panel planning-step" aria-label="Optimisation step" aria-live="polite">
          <span className="planning-step-count">Step {step! + 1} of {steps.length}</span>
          <strong>{current.title}</strong>
          <p>{current.detail}</p>
        </section>
      )}

      {/* ---- Legend (bottom left) ---- */}
      <section className="planning-panel planning-legend" aria-label="Planning legend">
        {step === null && view === "difference" ? (
          <>
            <strong>Time saved by the optimised layout</strong>
            <span><i style={{ background: "#2f7fd1" }} />A minute or more faster</span>
            <span><i style={{ background: "#7fb3e6" }} />15 to 60 s faster</span>
            <span><i style={{ background: "#56606d" }} />Within 15 s</span>
            <span><i style={{ background: "#e0533d" }} />Slower</span>
            <small>Column height: time saved or lost.</small>
          </>
        ) : (
          <>
            <strong>Response time, target {minutes(target)}</strong>
            {BANDS.map((b) => <span key={b.band}><i style={{ background: b.colour }} />{b.label(target)}</span>)}
            <span><i className="planning-outline" />Underserved: 50+ residents beyond the target</span>
            <small>Hexagons {plan?.hex_metres ?? 180} m centre to corner; each shows the median of its junctions. Column height: {METRES_PER_SECOND} m per second.</small>
          </>
        )}
        <strong>Stations</strong>
        <span><i style={{ background: SERVICE_COLOURS[settings.service] }} />Existing ({SERVICE_LABELS[settings.service].toLowerCase()})</span>
        <span><i style={{ background: PROPOSED_COLOUR }} />Proposed</span>
        <span><i style={{ background: CANDIDATE_COLOUR }} />Candidate (taller: better alone)</span>
        <span><i style={{ background: HIGHLIGHT_COLOUR }} />Most promising</span>
      </section>
      </div>

      <div className="planning-bar">
      {/* ---- Controls (below the map) ---- */}
      <section className="planning-card planning-controls" aria-label="Planning controls">
        <h3>Station planning</h3>
        <div className="planning-fields">
          <label>
            Service
            <select value={settings.service} onChange={(e) => change({ service: e.target.value as ServiceName })}>
              {(Object.keys(SERVICE_LABELS) as ServiceName[]).map((s) => <option key={s} value={s}>{SERVICE_LABELS[s]}</option>)}
            </select>
          </label>
          <label>
            Stations
            <select value={settings.stations} onChange={(e) => change({ stations: Number(e.target.value) })}>
              {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label>
            Target
            <select value={settings.targetMinutes} onChange={(e) => change({ targetMinutes: Number(e.target.value) })}>
              {[2, 3, 4, 5, 6, 8, 10, 15].map((m) => <option key={m} value={m}>{m} min</option>)}
            </select>
          </label>
          <label>
            Disruption
            <select value={settings.disruption} onChange={(e) => change({ disruption: e.target.value as Disruption })}>
              {(Object.keys(DISRUPTION_LABELS) as Disruption[]).map((d) => <option key={d} value={d}>{DISRUPTION_LABELS[d]}</option>)}
            </select>
          </label>
        </div>

        <div className="planning-views" role="group" aria-label="Layout shown">
          {(["existing", "optimised", "difference"] as View[]).map((v) => (
            <button key={v} type="button" aria-pressed={step === null && view === v}
              disabled={!plan || (v !== "optimised" && !existing)}
              onClick={() => { setStep(null); setPlaying(false); setView(v) }}>
              {v === "existing" ? "Existing" : v === "optimised" ? "Optimised" : "Difference"}
            </button>
          ))}
        </div>

        <div className="planning-play" role="group" aria-label="Optimisation steps">
          <button type="button" disabled={!plan} onClick={() => {
            if (playing) { setPlaying(false); return }
            setStep(0)
            setPlaying(true)
          }}>
            {playing ? "Pause" : "Play the optimisation"}
          </button>
          <button type="button" aria-label="Previous step" disabled={!plan || step === null || step === 0}
            onClick={() => { setPlaying(false); setStep((s) => (s === null ? 0 : Math.max(0, s - 1))) }}>‹</button>
          <button type="button" aria-label="Next step" disabled={!plan || step === steps.length - 1}
            onClick={() => { setPlaying(false); setStep((s) => (s === null ? 0 : Math.min(steps.length - 1, s + 1))) }}>›</button>
        </div>

        <fieldset className="planning-layers">
          <legend>Layers</legend>
          {([["coverage", "Response times"], ["underserved", "Underserved areas"], ["stations", "Stations"], ["buildings", "Buildings"]] as [keyof Visible, string][]).map(([key, label]) => (
            <label key={key}>
              <input type="checkbox" checked={visible[key]} onChange={(e) => setVisible((v) => ({ ...v, [key]: e.target.checked }))} />
              {label}
            </label>
          ))}
          <label>
            <input type="checkbox" checked={threeD} onChange={(e) => setThreeD(e.target.checked)} />
            3D columns
          </label>
        </fieldset>
        {loading && <p className="planning-note" role="status">Calculating on the road network…</p>}
        {error && <p className="planning-note planning-error" role="alert">{error}</p>}
      </section>

      {/* ---- Figures (below the map) ---- */}
      {plan && (
        <section className="planning-card planning-metrics" aria-label="Coverage figures">
          <h3>{SERVICE_LABELS[plan.service]}: existing against optimised</h3>
          <table aria-label="Existing and optimised layouts">
            <thead><tr><th scope="col">Measure</th><th scope="col">Existing</th><th scope="col">Optimised</th></tr></thead>
            <tbody>
              {comparison.map((row) => (
                <tr key={row.measure}>
                  <th scope="row">{row.measure}</th>
                  <td>{row.existing}</td>
                  <td className={row.better === "optimised" ? "planning-better" : row.better === "existing" ? "planning-worse" : ""}>{row.optimised}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="planning-note">
            {existing
              ? `Existing: ${plan.existing.length} ${plan.service} stations from OpenStreetMap.`
              : `No road ${plan.service} stations are mapped in OpenStreetMap for Norwich, so there is no existing layout to compare.`}{" "}
            Optimised: a fresh layout of the best {plan.optimised.length} of {plan.search.candidates} sites, in place of the
            existing ones ({plan.search.method}, {plan.search.possible_sets.toLocaleString("en-GB")} sets). {plan.closed_roads > 0 ? `${plan.closed_roads} roads closed. ` : ""}
            Response time = road distance from the nearest station at {plan.speed} m/s; no traffic.
          </p>
        </section>
      )}

      </div>
    </div>
  )
}
