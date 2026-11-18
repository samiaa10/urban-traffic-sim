// The operations view's monitoring pieces: the simulation timeline under the
// map, and the emergency and incident monitors in the right-hand column.
// Everything shown comes from the simulation's own state (each step's
// summary, the emergency status and the incident lists), nothing else.

import type { Sample, FloodInfo, SignalJunction, Speed } from "./live.ts"
import { SPEEDS, describeFlood, incidentMarks, signalQueues } from "./live.ts"
import type { EmergencyStatus } from "./simulationHelpers.ts"

const clock = (seconds: number) => {
  const whole = Math.max(0, Math.round(seconds))
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor((whole % 3600) / 60)
  const rest = String(whole % 60).padStart(2, "0")
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`
}

const WIDTH = 1000
const HEIGHT = 36

export function SimulationTimeline({
  history,
  simulationTime,
  paused,
  onPauseToggle,
  speed,
  onSpeed,
  active,
}: {
  history: Sample[]
  simulationTime: number | null
  paused: boolean
  onPauseToggle: () => void
  speed: Speed
  onSpeed: (speed: Speed) => void
  active: boolean
}) {
  const end = Math.max(60, history.length ? history[history.length - 1].time : 0)
  const start = history.length ? history[0].time : 0
  const span = Math.max(1, end - start)
  const peak = Math.max(1, ...history.map((s) => s.active_vehicles))
  const x = (t: number) => ((t - start) / span) * WIDTH
  const y = (v: number) => HEIGHT - 2 - (v / peak) * (HEIGHT - 6)
  const area = history.length > 1
    ? `M${x(history[0].time)},${HEIGHT} ` +
      history.map((s) => `L${x(s.time).toFixed(1)},${y(s.active_vehicles).toFixed(1)}`).join(" ") +
      ` L${x(history[history.length - 1].time)},${HEIGHT} Z`
    : ""
  const marks = incidentMarks(history)

  return (
    <div className="timeline" role="group" aria-label="Simulation timeline">
      <div className="timeline-controls">
        <button
          type="button"
          className="timeline-button"
          onClick={onPauseToggle}
          disabled={!active}
          aria-label={paused ? "Play" : "Pause playback"}
        >
          {paused ? "▶" : "❚❚"}
        </button>
        <div className="timeline-speeds" role="group" aria-label="Playback speed">
          {SPEEDS.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={speed === value}
              disabled={!active}
              onClick={() => onSpeed(value)}
            >
              {value}x
            </button>
          ))}
        </div>
        <span className="timeline-clock" aria-label="Simulated time">
          T+{simulationTime === null ? "0:00" : clock(simulationTime)}
        </span>
      </div>
      <svg
        className="timeline-track"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Vehicles driving over ${clock(span)} of simulated time, ${marks.length} incident(s) marked`}
      >
        {area && <path d={area} className="timeline-area" />}
        {marks.map((mark) => (
          <line key={`${mark.time}-${mark.label}`} x1={x(mark.time)} x2={x(mark.time)} y1={0} y2={HEIGHT}
            className="timeline-mark"><title>{`T+${clock(mark.time)}: ${mark.label}`}</title></line>
        ))}
        {history.length > 0 && <line x1={WIDTH - 1} x2={WIDTH - 1} y1={0} y2={HEIGHT} className="timeline-now" />}
      </svg>
    </div>
  )
}

const STATE_LABEL: Record<EmergencyStatus["state"], string> = {
  waiting: "Waiting for a vehicle",
  en_route: "En route",
  blocked: "Blocked by a closure",
  arrived: "Arrived",
}

export function EmergencyMonitor({ status, service }: { status: EmergencyStatus | null; service: string }) {
  return (
    <section className="monitor-card" aria-label="Emergency monitor">
      <h3>Emergency monitor</h3>
      {status === null ? (
        <p className="monitor-empty">Nothing under way</p>
      ) : (
        <dl className="monitor-figures">
          <div><dt>{service}</dt><dd className={`monitor-state monitor-state--${status.state}`}>{STATE_LABEL[status.state]}</dd></div>
          <div><dt>Elapsed</dt><dd>{clock(status.elapsed_seconds)}</dd></div>
          {status.state === "arrived" && status.response_time_seconds !== null ? (
            <div><dt>Response</dt><dd>{clock(status.response_time_seconds)}</dd></div>
          ) : (
            <>
              <div><dt>ETA</dt><dd>{clock(status.eta_seconds)}</dd></div>
              <div><dt>To go</dt><dd>{(status.remaining_metres / 1000).toFixed(1)} km</dd></div>
            </>
          )}
          <div><dt>Route changes</dt><dd>{status.reroutes}</dd></div>
        </dl>
      )}
    </section>
  )
}

export function IncidentMonitor({
  latest,
  floods,
  signals,
  simulationTime,
}: {
  latest: Sample | undefined
  floods: FloodInfo[]
  signals: SignalJunction[]
  simulationTime: number | null
}) {
  const queues = signalQueues(signals)
  const quiet = !latest || (latest.closed_roads === 0 && latest.accidents === 0 && floods.length === 0)
  return (
    <section className="monitor-card" aria-label="Incidents and disruptions">
      <h3>Incidents and disruptions</h3>
      <dl className="monitor-figures">
        <div><dt>Closed roads</dt><dd className={latest?.closed_roads ? "monitor-alert" : ""}>{latest?.closed_roads ?? 0}</dd></div>
        <div><dt>Accidents</dt><dd className={latest?.accidents ? "monitor-alert" : ""}>{latest?.accidents ?? 0}</dd></div>
        <div><dt>Floods</dt><dd className={floods.length ? "monitor-alert" : ""}>{floods.length}</dd></div>
        {signals.length > 0 && (
          <div><dt>Queued at signals</dt><dd>{queues.queued} at {queues.busy} junctions</dd></div>
        )}
        {latest?.stopped !== undefined && (
          <div><dt>Stopped in queues</dt><dd>{latest.stopped}</dd></div>
        )}
      </dl>
      {floods.length > 0 && (
        <ul className="monitor-list">
          {floods.map((flood) => (
            <li key={flood.id}>
              <span className={`monitor-dot monitor-dot--${flood.stage}`} aria-hidden="true" />
              Flood {flood.id}: {describeFlood(flood, simulationTime)}
            </li>
          ))}
        </ul>
      )}
      {quiet && <p className="monitor-empty">Network clear: no closures, accidents or floods</p>}
    </section>
  )
}
