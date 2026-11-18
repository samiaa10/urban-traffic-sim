// Live controls for the running simulation: pause and speed, fleets and a
// rush hour, accidents and closures picked on the map (and clearing them),
// and dynamic rerouting on or off. Everything goes to the run's own
// endpoints through `send`.

import { useState } from "react"

import { Icon, Panel } from "./components.tsx"
import {
  FLEET_SIZES,
  SPEEDS,
  accidentRequest,
  clearAccidentRequest,
  closureRequest,
  fleetRequest,
  reroutingRequest,
  bridgesRequest,
  clearFloodRequest,
  describeFlood,
  floodRequest,
  DEFAULT_FLOOD,
  FLOOD_SEVERITIES,
  type FloodInfo,
  type FloodSettings,
  type FloodSeverity,
  SIGNAL_CONTROLLERS,
  type SignalSettings,
  surgeRequest,
  type Severity,
  type Speed,
} from "./live.ts"
import { pickedRoadLabel, request, type NearestRoad } from "./scenarios.ts"

type Incident = { kind: "accident" | "closure"; road: NearestRoad; label: string }

export function LiveControls({
  active,
  paused,
  onPauseToggle,
  speed,
  onSpeed,
  send,
  onPickRoad,
  onMessage,
  signals,
  onApplySignals,
  floods,
  simulationTime,
  onFloodsChanged,
}: {
  // A run is going (controls that change it are enabled)
  active: boolean
  paused: boolean
  onPauseToggle: () => void
  speed: Speed
  onSpeed: (speed: Speed) => void
  // POST to the run's endpoint ([original, per-run path]); the JSON answer
  send: (paths: [string, string]) => Promise<Record<string, unknown>>
  // The next map click picks a road (as for scenarios)
  onPickRoad: (label: string | null, handler: ((lat: number, lon: number) => void) | null) => void
  onMessage: (text: string) => void
  // Traffic signals: what the run has, and how to change it
  signals: SignalSettings
  onApplySignals: (settings: SignalSettings) => Promise<{ junctions: number }>
  // Floods on the run, now, and telling the app when they change
  floods: FloodInfo[]
  simulationTime: number | null
  onFloodsChanged: (floods: FloodInfo[]) => void
}) {
  const [flood, setFlood] = useState<FloodSettings>(DEFAULT_FLOOD)
  const [bridgesOut, setBridgesOut] = useState(false)
  const changeFlood = (change: Partial<FloodSettings>) => setFlood((current) => ({ ...current, ...change }))

  const placeFlood = () => {
    onPickRoad("Flood centre", async (lat, lon) => {
      try {
        const answer = await send(floodRequest(lat, lon, flood))
        const created = answer.flood as FloodInfo
        onFloodsChanged([...floods, created])
        onMessage(
          `Flood: ${created.roads.length} roads ${created.closed ? "closed" : "flooded"} for ` +
          `${flood.minutes} min, then ${flood.recoveryMinutes} min to recover`,
        )
      } catch (caught) {
        onMessage(caught instanceof Error ? caught.message : "That did not work")
      }
    })
  }

  const clearFlood = async (id: number) => {
    try {
      await send(clearFloodRequest(id))
      onFloodsChanged(floods.filter((f) => f.id !== id))
      onMessage("Flood cleared: its roads are back to normal")
    } catch (caught) {
      onMessage(caught instanceof Error ? caught.message : "That did not work")
    }
  }

  const [signalDraft, setSignalDraft] = useState<SignalSettings>(signals)
  const changeSignals = (change: Partial<SignalSettings>) => setSignalDraft((current) => ({ ...current, ...change }))
  const applySignals = async () => {
    try {
      const answer = await onApplySignals(signalDraft)
      const label = SIGNAL_CONTROLLERS.find((c) => c.value === signalDraft.controller)?.label ?? ""
      onMessage(
        signalDraft.controller === "none"
          ? "Traffic signals off"
          : `${label} signals at ${answer.junctions} junctions` +
            (signalDraft.priority ? ", with emergency priority" : ""),
      )
    } catch (caught) {
      onMessage(caught instanceof Error ? caught.message : "That did not work")
    }
  }

  const [fleetSize, setFleetSize] = useState(500)
  const [severity, setSeverity] = useState<Severity>("severe")
  const [minutes, setMinutes] = useState(15)
  const [rerouting, setRerouting] = useState(true)
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [seed, setSeed] = useState(1)

  const act = async (paths: [string, string], done: (answer: Record<string, unknown>) => string) => {
    try {
      onMessage(done(await send(paths)))
    } catch (caught) {
      onMessage(caught instanceof Error ? caught.message : "That did not work")
    }
  }

  const pick = (kind: Incident["kind"]) => {
    onPickRoad(kind === "accident" ? "Road for the accident" : "Road to close", async (lat, lon) => {
      try {
        const road = await request<NearestRoad>(`/scenarios/nearest-road?latitude=${lat}&longitude=${lon}`)
        const label = pickedRoadLabel(road)
        const paths = kind === "accident"
          ? accidentRequest(road.start, road.destination, severity, minutes)
          : closureRequest(road.start, road.destination, false)
        await send(paths)
        setIncidents((current) => [...current, { kind, road, label }])
        onMessage(
          kind === "accident"
            ? `${severity[0].toUpperCase()}${severity.slice(1)} accident on ${label} for ${minutes} min`
            : `Closed ${label}: vehicles that were using it re-plan`
        )
      } catch (caught) {
        onMessage(caught instanceof Error ? caught.message : "Could not add that incident")
      }
    })
  }

  const clear = async (incident: Incident) => {
    const { start, destination } = incident.road
    await act(
      incident.kind === "accident" ? clearAccidentRequest(start, destination) : closureRequest(start, destination, true),
      () => `${incident.kind === "accident" ? "Cleared the accident on" : "Reopened"} ${incident.label}`
    )
    setIncidents((current) => current.filter((item) => item !== incident))
  }

  return (
    <Panel id="live" title="Live controls" caption="Change the running simulation">
      <div className="fields">
        <button type="button" className="btn" onClick={onPauseToggle} disabled={!active}
          aria-pressed={paused}>
          <Icon name={paused ? "play" : "pause"} size={16} />
          {paused ? "Resume" : "Pause"}
        </button>
        <label className="field">
          Speed
          <select aria-label="Simulation speed" value={speed} disabled={!active}
            onChange={(event) => onSpeed(Number(event.target.value) as Speed)}>
            {SPEEDS.map((value) => <option key={value} value={value}>{value}x</option>)}
          </select>
        </label>
      </div>

      <div className="divider" />

      <div className="fields">
        <label className="field">
          Vehicles
          <select aria-label="Fleet size" value={fleetSize} disabled={!active}
            onChange={(event) => setFleetSize(Number(event.target.value))}>
            {FLEET_SIZES.map((value) => <option key={value} value={value}>{value.toLocaleString("en-GB")}</option>)}
          </select>
        </label>
        <button type="button" className="btn" disabled={!active}
          onClick={() => {
            setSeed((value) => value + 1)
            void act(fleetRequest(fleetSize, seed), (answer) =>
              `Added ${answer.spawned ?? fleetSize} vehicles with trips across Norwich`)
          }}>
          <Icon name="route" size={16} />
          Add vehicles
        </button>
      </div>
      <button type="button" className="btn btn--block" disabled={!active}
        onClick={() => void act(surgeRequest("inbound"), () =>
          "Rush hour: 60 trips a minute into the city centre for 15 minutes")}>
        <Icon name="chart" size={16} />
        Start a rush hour into the centre
      </button>

      <div className="divider" />

      <div className="fields">
        <label className="field">
          Accident
          <select aria-label="Accident severity" value={severity} disabled={!active}
            onChange={(event) => setSeverity(event.target.value as Severity)}>
            <option value="minor">Minor</option>
            <option value="moderate">Moderate</option>
            <option value="severe">Severe</option>
          </select>
        </label>
        <label className="field">
          Lasting
          <select aria-label="Accident duration" value={minutes} disabled={!active}
            onChange={(event) => setMinutes(Number(event.target.value))}>
            {[5, 15, 30].map((value) => <option key={value} value={value}>{value} min</option>)}
          </select>
        </label>
      </div>
      <div className="fields">
        <button type="button" className="btn" disabled={!active} onClick={() => pick("accident")}>
          <Icon name="alert" size={16} />
          Add an accident
        </button>
        <button type="button" className="btn" disabled={!active} onClick={() => pick("closure")}>
          <Icon name="close" size={16} />
          Close a road
        </button>
      </div>

      {incidents.length > 0 && (
        <ul className="incident-list" aria-label="Your incidents">
          {incidents.map((incident) => (
            <li key={`${incident.kind}-${incident.road.start}-${incident.road.destination}`}>
              <span>{incident.kind === "accident" ? "Accident" : "Closed"}: {incident.label}</span>
              <button type="button" className="btn btn--quiet" onClick={() => void clear(incident)}>
                {incident.kind === "accident" ? "Clear" : "Reopen"}
              </button>
            </li>
          ))}
        </ul>
      )}

      <fieldset className="live-signals" disabled={!active}>
        <legend>Floods and failures</legend>
        <p className="hint">
          A flood closes or slows every road inside a circle you place on the map, then recovers
          gradually. It is a transport disruption you choose, not a flood forecast.
        </p>
        <div className="fields">
          <label className="field">
            Radius
            <select aria-label="Flood radius" value={flood.radiusMetres}
              onChange={(event) => changeFlood({ radiusMetres: Number(event.target.value) })}>
              {[150, 250, 400, 600].map((m) => <option key={m} value={m}>{m} m</option>)}
            </select>
          </label>
          <label className="field">
            Severity
            <select aria-label="Flood severity" value={flood.severity}
              onChange={(event) => changeFlood({ severity: event.target.value as FloodSeverity })}>
              {FLOOD_SEVERITIES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </label>
          <label className="field">
            Lasts
            <select aria-label="Flood duration" value={flood.minutes}
              onChange={(event) => changeFlood({ minutes: Number(event.target.value) })}>
              {[10, 20, 30, 60].map((m) => <option key={m} value={m}>{m} min</option>)}
            </select>
          </label>
          <label className="field">
            Recovery
            <select aria-label="Flood recovery" value={flood.recoveryMinutes}
              onChange={(event) => changeFlood({ recoveryMinutes: Number(event.target.value) })}>
              {[0, 5, 10, 20].map((m) => <option key={m} value={m}>{m} min</option>)}
            </select>
          </label>
        </div>
        <button type="button" className="btn btn--block" onClick={placeFlood}>
          Place a flood on the map
        </button>
        {floods.length > 0 && (
          <ul className="incident-list" aria-label="Floods">
            {floods.map((f) => (
              <li key={f.id}>
                <span>Flood {f.id}: {describeFlood(f, simulationTime)}</span>
                <button type="button" className="btn btn--quiet" onClick={() => void clearFlood(f.id)}>
                  Clear
                </button>
              </li>
            ))}
          </ul>
        )}
        <label className="toggle">
          <input type="checkbox" checked={bridgesOut}
            onChange={(event) => {
              const fail = event.target.checked
              setBridgesOut(fail)
              void act(bridgesRequest(fail), (answer) =>
                fail
                  ? `${answer.bridges} bridges out of use: traffic reroutes around them`
                  : "Bridges back in use")
            }} />
          Bridges out of use
        </label>
      </fieldset>

      <fieldset className="live-signals" disabled={!active}>
        <legend>Traffic signals</legend>
        <p className="hint">
          At the junctions OpenStreetMap shows with traffic lights. Timings are experimental
          defaults, not Norwich&apos;s real ones.
        </p>
        <div className="fields">
          <label className="field">
            Control
            <select
              aria-label="Signal control"
              value={signalDraft.controller}
              onChange={(event) => changeSignals({ controller: event.target.value as SignalSettings["controller"] })}
            >
              {SIGNAL_CONTROLLERS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          {signalDraft.controller === "fixed" && (
            <label className="field">
              Green per phase (s)
              <input type="number" min={5} max={120} value={signalDraft.green}
                onChange={(event) => changeSignals({ green: Number(event.target.value) })} />
            </label>
          )}
          {signalDraft.controller === "adaptive" && (
            <>
              <label className="field">
                Min green (s)
                <input type="number" min={3} max={60} value={signalDraft.minGreen}
                  onChange={(event) => changeSignals({ minGreen: Number(event.target.value) })} />
              </label>
              <label className="field">
                Max green (s)
                <input type="number" min={5} max={180} value={signalDraft.maxGreen}
                  onChange={(event) => changeSignals({ maxGreen: Number(event.target.value) })} />
              </label>
            </>
          )}
        </div>
        {signalDraft.controller !== "none" && (
          <label className="toggle">
            <input type="checkbox" checked={signalDraft.priority}
              onChange={(event) => changeSignals({ priority: event.target.checked })} />
            Emergency vehicle priority
          </label>
        )}
        <button type="button" className="btn btn--block" onClick={() => void applySignals()}>
          Apply signals
        </button>
        {signals.controller !== "none" && (
          <p className="hint">
            Now: {SIGNAL_CONTROLLERS.find((c) => c.value === signals.controller)?.label.toLowerCase()}
            {signals.priority ? " with emergency priority" : ""}
          </p>
        )}
      </fieldset>

      <label className="toggle">
        <input type="checkbox" checked={rerouting} disabled={!active}
          onChange={(event) => {
            const enabled = event.target.checked
            setRerouting(enabled)
            void act(reroutingRequest(enabled), () =>
              enabled ? "Dynamic rerouting on" : "Dynamic rerouting off (closures still reroute)")
          }} />
        Dynamic rerouting
      </label>
    </Panel>
  )
}
