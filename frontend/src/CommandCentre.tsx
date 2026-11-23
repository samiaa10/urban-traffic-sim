// The emergency response command centre: a run of the real simulation with
// a standing fleet (OpenStreetMap's fire and police stations, the
// optimiser's ambulance standby points) and ordinary traffic. Incidents
// are reported by clicking the map; the backend sends units by road travel
// time, they drive (and re-plan) in the simulation, and every figure,
// timeline and replay here comes from GET /simulations/{id}/command and
// /command/replay (backend simulation/command.py; logic in command.ts).

import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react"

import { API_URL } from "./config.ts"
import { setActiveRun } from "./activeRun.ts"
import CommandMap2D from "./CommandMap2D.tsx"
import {
  KINDS,
  SERVICE_LABELS,
  STATUS_LABELS,
  UNIT_LABELS,
  canResolve,
  clock,
  commandMapData,
  kindOf,
  metricTiles,
  replaySpan,
  statusAt,
  unitsByService,
  type CommandState,
  type Kind,
  type Replay,
} from "./command.ts"
import type { FloodInfo } from "./live.ts"
import type { CameraFocus } from "./Map3D.tsx"
import type { MapState } from "./mapData.ts"
import { SERVICE_MARKERS } from "./mapData.ts"
import type { Coordinate, Vehicle } from "./simulationHelpers.ts"

const Map3D = lazy(() => import("./Map3D.tsx"))

const TICK_MS = 1000
const SPEEDS = [1, 5, 10, 30] as const
const REPLAY_SPEEDS = [10, 30, 60] as const
// How close the camera goes to an incident
const INCIDENT_ZOOM = 15

async function call<T>(path: string, method = "GET"): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { method })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`)
  }
  return body as T
}

// attachTo: a run already set up with a command centre (a scenario the
// analyst started), watched instead of starting a new one
export function CommandCentre({ attachTo = null }: { attachTo?: string | null } = {}) {
  const [runId, setRunId] = useState<string | null>(attachTo)
  const [starting, setStarting] = useState(false)
  const [settings, setSettings] = useState({ units: 2, traffic: 800 })
  const [state, setState] = useState<CommandState | null>(null)
  const [vehicles, setVehicles] = useState<Vehicle[]>([])
  const [congestion, setCongestion] = useState<MapState["congestion"]>([])
  const [floods, setFloods] = useState<FloodInfo[]>([])
  const [paused, setPaused] = useState(false)
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(5)
  const [armed, setArmed] = useState<Kind | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [focus, setFocus] = useState<CameraFocus | null>(null)
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  const [flat, setFlat] = useState<string | null>(null)
  const [lost, setLost] = useState(false)
  // Replay: the recording, the time shown, and whether it plays
  const [replay, setReplay] = useState<Replay | null>(null)
  const [replayTime, setReplayTime] = useState(0)
  const [replaySpeed, setReplaySpeed] = useState<(typeof REPLAY_SPEEDS)[number]>(30)
  const [replayPlaying, setReplayPlaying] = useState(false)

  // The polling loop reads these
  const live = useRef({ runId, paused, speed, replaying: false, floods: false })
  useEffect(() => {
    live.current = {
      runId, paused, speed, replaying: replay !== null,
      floods: (state?.incidents ?? []).some((i) => i.kind === "flood" && i.status !== "resolved"),
    }
  })

  // One update at a time, once a second: step the run (unless paused or
  // replaying), then read the command centre, congestion and floods
  useEffect(() => {
    if (!runId) return
    let inFlight = false
    let stopped = false
    const run = `/simulations/${runId}`
    const timer = window.setInterval(async () => {
      if (inFlight) return
      inFlight = true
      const now = live.current
      try {
        if (!now.paused && !now.replaying) {
          const step = await call<{ vehicles: Vehicle[] }>(`${run}/step?seconds=${now.speed}`, "POST")
          if (stopped) return
          setVehicles(step.vehicles)
        }
        const [command, roads] = await Promise.all([
          call<CommandState>(`${run}/command`),
          call<{ congestion: MapState["congestion"] }>(`${run}/congestion?geometry=true`).catch(() => null),
        ])
        if (stopped) return
        setState(command)
        if (roads) setCongestion(roads.congestion ?? [])
        if (now.floods) {
          const flooded = await call<{ floods: FloodInfo[] }>(`${run}/incidents/floods`).catch(() => null)
          if (!stopped && flooded) setFloods(flooded.floods ?? [])
        } else {
          setFloods([])
        }
        setLost(false)
      } catch {
        if (!stopped) setLost(true)
      } finally {
        inFlight = false
      }
    }, TICK_MS)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [runId])

  // Replay playing: replaySpeed simulated seconds per 100 ms tick / 10
  useEffect(() => {
    if (!replay || !replayPlaying) return
    const [, end] = replaySpan(replay)
    const timer = window.setInterval(() => {
      setReplayTime((t) => {
        const next = Math.min(end, t + replaySpeed / 10)
        if (next >= end) setReplayPlaying(false)
        return next
      })
    }, 100)
    return () => clearInterval(timer)
  }, [replay, replayPlaying, replaySpeed])

  async function start() {
    setStarting(true)
    setMessage(null)
    try {
      const started = await call<CommandState & { simulation_id: string }>(
        `/command/start?vehicles_per_station=${settings.units}&background_vehicles=${settings.traffic}`, "POST",
      )
      setState(started)
      setRunId(started.simulation_id)
      setActiveRun(started.simulation_id)
      // The whole fleet in view to begin with
      setFocus({ key: "stations", positions: started.stations.map((s) => [s.longitude, s.latitude] as [number, number]), maxZoom: 14 })
      setMessage({ text: `Command centre open: ${started.units.length} units at ${started.stations.length} stations. Choose an incident type, then click the map.`, error: false })
    } catch (error) {
      setMessage({ text: `Could not start the command centre: ${error instanceof Error ? error.message : error}`, error: true })
    } finally {
      setStarting(false)
    }
  }

  async function report(kind: Kind, [latitude, longitude]: Coordinate) {
    if (!runId) return
    setArmed(null)
    try {
      const answer = await call<{ incident: CommandState["incidents"][number]; dispatched: string[] }>(
        `/simulations/${runId}/command/incidents?kind=${kind}&latitude=${latitude}&longitude=${longitude}`, "POST",
      )
      const incident = answer.incident
      setSelected(incident.id)
      setFocus({ key: `incident-${incident.id}`, positions: [[incident.longitude, incident.latitude]], maxZoom: INCIDENT_ZOOM })
      const sent = answer.dispatched.filter((s) => s === "dispatched").length
      const queued = answer.dispatched.filter((s) => s === "queued").length
      setMessage({
        text: `#${incident.id} ${incident.label}: ` + (answer.dispatched.length === 0
          ? "road closed."
          : `${sent} unit${sent === 1 ? "" : "s"} dispatched` + (queued ? `, ${queued} queued (none free)` : "") +
            (answer.dispatched.length > sent + queued ? ", and a service no unit could be sent from" : "") + "."),
        error: false,
      })
      setState(await call<CommandState>(`/simulations/${runId}/command`))
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : String(error), error: true })
    }
  }

  async function resolve(id: number) {
    if (!runId) return
    try {
      await call(`/simulations/${runId}/command/incidents/${id}/resolve`, "POST")
      setState(await call<CommandState>(`/simulations/${runId}/command`))
      setMessage({ text: `#${id} resolved: units at the scene are returning to their stations.`, error: false })
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : String(error), error: true })
    }
  }

  async function openReplay() {
    if (!runId) return
    try {
      const recorded = await call<Replay>(`/simulations/${runId}/command/replay`)
      setReplay(recorded)
      setReplayTime(replaySpan(recorded)[0])
      setReplayPlaying(true)
    } catch (error) {
      setMessage({ text: error instanceof Error ? error.message : String(error), error: true })
    }
  }

  // Each selection moves the camera, even back to the same incident
  const focusCount = useRef(0)
  const select = (id: number) => {
    const incident = state?.incidents.find((i) => i.id === id)
    setSelected(id)
    focusCount.current += 1
    if (incident) setFocus({ key: `incident-${id}-${focusCount.current}`, positions: [[incident.longitude, incident.latitude]], maxZoom: INCIDENT_ZOOM })
  }

  const data = useMemo(
    () => commandMapData({
      state, vehicles, congestion, floods, selected,
      replay: replay ? { data: replay, time: replayTime } : null,
    }),
    [state, vehicles, congestion, floods, selected, replay, replayTime],
  )

  const onMapClick = (location: Coordinate) => {
    if (armed && !replay) void report(armed, location)
  }

  // ---------------- Before the shift starts ----------------
  if (!runId) {
    return (
      <div className="command command--idle" aria-label="Emergency response command centre">
        <section className="command-start">
          <h3>Emergency response command centre</h3>
          <p>
            Starts a run of the simulation with a standing fleet: units at Norwich's three fire and four police
            stations (from OpenStreetMap) and at three ambulance standby points chosen by the station optimiser
            (no road ambulance station is mapped in Norwich), with ordinary traffic on the roads. Report incidents
            on the map; units are sent by road travel time, drive through the traffic and re-plan around
            closures and congestion.
          </p>
          <div className="command-start-fields">
            <label>
              Units per station
              <select value={settings.units} onChange={(e) => setSettings((s) => ({ ...s, units: Number(e.target.value) }))}>
                {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <label>
              Background traffic
              <select value={settings.traffic} onChange={(e) => setSettings((s) => ({ ...s, traffic: Number(e.target.value) }))}>
                {[0, 400, 800, 1500].map((n) => <option key={n} value={n}>{n} vehicles</option>)}
              </select>
            </label>
          </div>
          <button type="button" className="btn btn--emergency" onClick={() => void start()} disabled={starting} aria-busy={starting}>
            {starting ? "Starting…" : "Open the command centre"}
          </button>
          {message && <p className={message.error ? "command-message command-message--error" : "command-message"} role={message.error ? "alert" : "status"}>{message.text}</p>}
        </section>
      </div>
    )
  }

  const metrics = state?.metrics
  const incidents = [...(state?.incidents ?? [])].reverse()
  const chosen = (replay?.incidents ?? state?.incidents ?? []).find((i) => i.id === selected) ?? null
  const span = replay ? replaySpan(replay) : null

  return (
    <div className="command" aria-label="Emergency response command centre">
      {/* ---- Response figures ---- */}
      <div className="command-kpis" role="group" aria-label="Response performance">
        {metrics && metricTiles(metrics).map((tile) => (
          <div key={tile.label} className={`command-kpi command-kpi--${tile.tone}`}>
            <span>{tile.label}</span>
            <strong>{tile.value}</strong>
            <small>{tile.detail}</small>
          </div>
        ))}
      </div>

      {/* ---- The map ---- */}
      <div className="command-stage">
        {flat ? (
          <CommandMap2D data={data} onClick={onMapClick} />
        ) : (
          <Suspense fallback={<div className="map map-loading">Loading the 3D map…</div>}>
            <Map3D
              data={data}
              onClick={onMapClick}
              onFailed={(reason) => setFlat(reason)}
              inspecting={false}
              onInspectingChange={() => {}}
              focus={focus}
            />
          </Suspense>
        )}

        <section className="command-panel command-report" aria-label="Report an incident">
          <h3>Report an incident</h3>
          <div className="command-kinds">
            {KINDS.map((k) => (
              <button key={k.kind} type="button" aria-pressed={armed === k.kind} disabled={replay !== null}
                onClick={() => setArmed(armed === k.kind ? null : k.kind)}
                title={(k.services.length ? `Sends: ${k.services.map((s) => SERVICE_LABELS[s].toLowerCase()).join(" and ")}` : "Sends no unit") + (k.effect ? `; ${k.effect}` : "")}>
                <i style={{ background: k.colour }} aria-hidden="true" />{k.label}
              </button>
            ))}
          </div>
          <p className="command-note">
            {armed
              ? `Click the map where the ${kindOf(armed).label.toLowerCase()} is.`
              : "Choose a type, then click the map."}
          </p>
        </section>

        {/* ---- Live playback or replay ---- */}
        <section className="command-panel command-playback" aria-label={replay ? "Replay" : "Simulation playback"}>
          {replay && span ? (
            <>
              <button type="button" onClick={() => setReplayPlaying(!replayPlaying)}>{replayPlaying ? "Pause replay" : "Play replay"}</button>
              <input type="range" aria-label="Replay time" min={span[0]} max={span[1]} step={1} value={replayTime}
                onChange={(e) => { setReplayPlaying(false); setReplayTime(Number(e.target.value)) }} />
              <span className="command-clock">T+{clock(replayTime)}</span>
              <div className="command-speeds" role="group" aria-label="Replay speed">
                {REPLAY_SPEEDS.map((s) => <button key={s} type="button" aria-pressed={replaySpeed === s} onClick={() => setReplaySpeed(s)}>{s}x</button>)}
              </div>
              <button type="button" onClick={() => { setReplay(null); setReplayPlaying(false) }}>Back to live</button>
            </>
          ) : (
            <>
              <button type="button" onClick={() => setPaused(!paused)}>{paused ? "Resume" : "Pause"}</button>
              <div className="command-speeds" role="group" aria-label="Simulation speed">
                {SPEEDS.map((s) => <button key={s} type="button" aria-pressed={speed === s} onClick={() => setSpeed(s)}>{s}x</button>)}
              </div>
              <span className="command-clock" aria-label="Simulated time">T+{clock(state?.simulation_time)}</span>
              <button type="button" onClick={() => void openReplay()} disabled={!state?.incidents.length}>Replay</button>
            </>
          )}
        </section>
      </div>

      {flat && <p className="command-message command-message--error" role="alert">The 3D map could not start ({flat}), so the 2D map is shown.</p>}
      {lost && <p className="command-message command-message--error" role="alert">Lost contact with the simulation; retrying every second.</p>}
      {message && <p className={message.error ? "command-message command-message--error" : "command-message"} role={message.error ? "alert" : "status"}>{message.text}</p>}

      {/* ---- Incidents, the selected timeline, units ---- */}
      <div className="command-board">
        <section className="command-card" aria-label="Incident log">
          <h3>Incidents</h3>
          {incidents.length === 0 ? (
            <p className="command-empty">No incidents yet.</p>
          ) : (
            <ul className="command-incidents">
              {incidents.map((incident) => {
                const status = replay ? statusAt(incident, replayTime) : incident.status
                return (
                  <li key={incident.id}>
                    <button type="button" aria-pressed={incident.id === selected} onClick={() => select(incident.id)}>
                      <i style={{ background: kindOf(incident.kind).colour }} aria-hidden="true" />
                      <span className="command-incident-name">#{incident.id} {incident.label}</span>
                      <span className={`command-status command-status--${status ?? "future"}`}>
                        {status ? STATUS_LABELS[status] : "Not yet"}
                      </span>
                      <small>Reported T+{clock(incident.created_at)}</small>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <section className="command-card" aria-label="Incident timeline">
          <h3>{chosen ? `#${chosen.id} ${chosen.label}` : "Timeline"}</h3>
          {chosen ? (
            <>
              <ol className="command-timeline">
                {chosen.timeline.map((event, index) => {
                  const later = replay !== null && event.time > replayTime
                  return (
                    <li key={index} className={`command-event command-event--${event.event}${later ? " command-event--later" : ""}`}>
                      <time>T+{clock(event.time)}</time>
                      <span>{event.label}</span>
                      {event.within_target !== undefined && (
                        <em className={event.within_target ? "command-within" : "command-missed"}>
                          {event.within_target ? "within target" : "missed target"}
                        </em>
                      )}
                    </li>
                  )
                })}
              </ol>
              {!replay && chosen.status !== "resolved" && (
                <button type="button" className="btn btn--secondary" disabled={!canResolve(chosen)} onClick={() => void resolve(chosen.id)}
                  title={canResolve(chosen) ? "" : "Units are still on their way"}>
                  {chosen.kind === "road_closure" ? "Reopen the road" : "Resolve and stand units down"}
                </button>
              )}
            </>
          ) : (
            <p className="command-empty">Select an incident to see its timeline.</p>
          )}
        </section>

        <section className="command-card" aria-label="Units">
          <h3>Units</h3>
          {unitsByService(state?.units ?? []).map(([service, units]) => (
            <div key={service} className="command-units">
              <h4>
                <i style={{ background: SERVICE_MARKERS[service].colour }} aria-hidden="true" />
                {SERVICE_LABELS[service]}s: {units.filter((u) => u.state === "available" || u.state === "returning").length} of {units.length} free
              </h4>
              <table aria-label={`${SERVICE_LABELS[service]} units`}>
                <tbody>
                  {units.map((unit) => (
                    <tr key={unit.vehicle_id}>
                      <th scope="row">{SERVICE_MARKERS[service].letter}{unit.vehicle_id}</th>
                      <td className={`command-unit command-unit--${unit.state}`}>{UNIT_LABELS[unit.state]}</td>
                      <td>{unit.incident_id ? `#${unit.incident_id}` : unit.station}</td>
                      <td>{unit.eta_seconds !== null ? `ETA ${clock(unit.eta_seconds)}` : unit.reroutes ? `${unit.reroutes} reroute${unit.reroutes === 1 ? "" : "s"}` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </section>
      </div>
    </div>
  )
}
