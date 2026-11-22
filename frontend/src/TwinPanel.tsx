// The 3D map's "Digital twin" panel: the sun over Norwich now or at any
// hour, the live weather (Open-Meteo), terrain, population and emergency
// coverage layers, congestion in 3D, viewpoints with a fly-over, and a
// performance mode for weaker devices. Logic in twin.ts and twinLayers.ts.

import { useEffect, useMemo, useRef, useState } from "react"
import type { Map as MapLibreMap } from "maplibre-gl"

import { API_URL } from "./config.ts"
import type { FeatureCollection } from "./mapData.ts"
import type { Plan } from "./planning.ts"
import {
  VIEWPOINTS,
  atmosphere,
  congestionWalls,
  existingCoverage,
  populationFeatures,
  preferLite,
  saveViewpoints,
  softwareRenderer,
  savedViewpoints,
  sunPosition,
  weatherEffect,
  type Viewpoint,
  type Weather,
} from "./twin.ts"
import { TwinLayers, type TwinMap } from "./twinLayers.ts"

const WEATHER_MS = 10 * 60 * 1000

// UK clock time today at `hour` (GMT or BST, as Norwich keeps it)
function ukDate(hour: number): Date {
  const now = new Date()
  const zone = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", timeZoneName: "short" })
    .formatToParts(now).find((p) => p.type === "timeZoneName")?.value
  const offset = zone === "BST" ? 1 : 0
  const date = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 0, 0))
  date.setUTCMinutes(Math.round((hour - offset) * 60))
  return date
}

// The UK clock's hour (with minutes as a fraction) at a moment
function ukHour(date: Date): number {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(date)
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0)
  return part("hour") + part("minute") / 60
}

const ukClock = (date: Date) =>
  date.toLocaleTimeString("en-GB", { timeZone: "Europe/London", hour: "2-digit", minute: "2-digit" })

// The graphics card's name, if the browser tells it
function renderer(): string | null {
  try {
    const gl = document.createElement("canvas").getContext("webgl")
    const info = gl?.getExtension("WEBGL_debug_renderer_info")
    return gl && info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : null
  } catch {
    return null
  }
}

let profile: { cores?: number; memoryGb?: number; reducedMotion?: boolean; software?: boolean } | null = null

function deviceProfile() {
  if (profile) return profile
  const nav = navigator as Navigator & { deviceMemory?: number }
  profile = {
    cores: nav.hardwareConcurrency,
    memoryGb: nav.deviceMemory,
    reducedMotion: typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches,
    software: softwareRenderer(renderer()),
  }
  return profile
}

const TERRAIN_KEY = "norwich-sim-terrain"

function savedTerrain(): boolean {
  try {
    return localStorage.getItem(TERRAIN_KEY) === "1"
  } catch {
    return false
  }
}

export function TwinPanel({
  map,
  congestion,
  vehicles,
  open,
  onClose,
}: {
  map: MapLibreMap | null
  congestion: FeatureCollection
  vehicles: number
  open: boolean
  onClose: () => void
}) {
  const layers = useRef<TwinLayers | null>(null)
  const [lite, setLite] = useState(() => preferLite(deviceProfile(), vehicles))
  // Terrain is opt-in (it costs every frame), and remembered
  const [terrain, setTerrain] = useState(savedTerrain)
  const [hour, setHour] = useState<number | null>(null)   // null: now
  const [now, setNow] = useState(() => new Date())
  const [weatherOn, setWeatherOn] = useState(true)
  const [weather, setWeather] = useState<Weather | null>(null)
  const [weatherError, setWeatherError] = useState("")
  const [show, setShow] = useState({ population: false, coverage: false, congestion: false })
  const [service, setService] = useState<"fire" | "police">("fire")
  const [plans, setPlans] = useState<Partial<Record<"fire" | "police", Plan>>>({})
  const [planError, setPlanError] = useState("")
  const [saved, setSaved] = useState<Viewpoint[]>(savedViewpoints)
  const [touring, setTouring] = useState(false)

  // The clock moves on while showing "now"
  useEffect(() => {
    if (hour !== null) return
    const timer = window.setInterval(() => setNow(new Date()), 60_000)
    return () => clearInterval(timer)
  }, [hour])

  // Live weather, refreshed every 10 minutes
  useEffect(() => {
    if (!weatherOn) return
    let current = true
    const load = () => fetch(`${API_URL}/city/weather`)
      .then(async (r) => {
        const body = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(body.detail ?? "Live weather is unavailable")
        return body as Weather
      })
      .then((w) => { if (current) { setWeather(w); setWeatherError("") } })
      .catch((e) => { if (current) setWeatherError(e instanceof Error ? e.message : String(e)) })
    void load()
    const timer = window.setInterval(load, WEATHER_MS)
    return () => { current = false; clearInterval(timer) }
  }, [weatherOn])

  // Coverage and population come from the planning calculation (cached
  // by the backend), fetched the first time they are switched on
  const wanted = show.coverage ? service : show.population ? "fire" : null
  useEffect(() => {
    if (!wanted || plans[wanted]) return
    let current = true
    fetch(`${API_URL}/optimiser/plan?service=${wanted}&stations=1&target_seconds=${wanted === "fire" ? 480 : 900}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("The coverage calculation failed"))))
      .then((plan: Plan) => { if (current) { setPlans((p) => ({ ...p, [wanted]: plan })); setPlanError("") } })
      .catch((e) => { if (current) setPlanError(e instanceof Error ? e.message : String(e)) })
    return () => { current = false }
  }, [wanted, plans])

  // Layers on the map, once it has loaded
  useEffect(() => {
    if (!map) return
    const firstSymbol = map.getStyle().layers.find((layer) => layer.type === "symbol")?.id
    const twin = new TwinLayers(map as unknown as TwinMap, firstSymbol)
    twin.create()
    layers.current = twin
    return () => {
      layers.current = null
      try { twin.remove() } catch { /* the map is already gone */ }
    }
  }, [map])

  const date = hour === null ? now : ukDate(hour)
  const sun = useMemo(() => sunPosition(date), [date])
  const effect = weatherEffect(weatherOn ? weather : null)

  useEffect(() => {
    const twin = layers.current
    if (!twin) return
    const sky = atmosphere(sun, weatherOn ? weather : null)
    twin.setAtmosphere(sky.sky, sky.light)
  }, [sun, weather, weatherOn, map])

  useEffect(() => {
    layers.current?.setTerrain(terrain && !lite)
    try {
      localStorage.setItem(TERRAIN_KEY, terrain ? "1" : "0")
    } catch {
      // Private browsing: for this visit only
    }
  }, [terrain, lite, map])

  useEffect(() => {
    if (!map) return
    map.setPixelRatio(lite ? 1 : Math.min(window.devicePixelRatio || 1, 2))
  }, [lite, map])

  useEffect(() => {
    const twin = layers.current
    if (!twin) return
    const plan = plans[service]
    twin.update("coverage", plan ? existingCoverage(plan) ?? { type: "FeatureCollection", features: [] } : { type: "FeatureCollection", features: [] })
    const people = plans.fire ?? plans.police
    twin.update("population", people ? populationFeatures(people) : { type: "FeatureCollection", features: [] })
    twin.show("coverage", show.coverage)
    twin.show("population", show.population)
    twin.show("congestion", show.congestion)
  }, [show, plans, service, map])

  useEffect(() => {
    if (show.congestion) layers.current?.update("congestion", congestionWalls(congestion))
  }, [congestion, show.congestion, map])

  // The fly-over: each viewpoint in turn
  useEffect(() => {
    if (!touring || !map) return
    let index = 0
    let stopped = false
    const next = () => {
      if (stopped) return
      const view = VIEWPOINTS[index % VIEWPOINTS.length]
      index += 1
      map.flyTo({ center: view.center, zoom: view.zoom, pitch: view.pitch, bearing: view.bearing, duration: 6000, essential: true })
      map.once("moveend", () => { if (!stopped) window.setTimeout(next, 1200) })
    }
    next()
    const stop = () => setTouring(false)
    map.once("mousedown", stop)
    map.once("touchstart", stop)
    return () => { stopped = true; map.off("mousedown", stop); map.off("touchstart", stop) }
  }, [touring, map])

  const go = (view: Viewpoint) => {
    setTouring(false)
    map?.flyTo({ center: view.center, zoom: view.zoom, pitch: view.pitch, bearing: view.bearing, duration: 2800, essential: true })
  }

  const saveHere = () => {
    if (!map) return
    const centre = map.getCenter()
    const view: Viewpoint = {
      name: `Saved view ${saved.length + 1}`,
      center: [Number(centre.lng.toFixed(5)), Number(centre.lat.toFixed(5))],
      zoom: Number(map.getZoom().toFixed(2)), pitch: Math.round(map.getPitch()), bearing: Math.round(map.getBearing()),
    }
    const next = [...saved, view]
    setSaved(next)
    saveViewpoints(next)
  }

  const forget = (index: number) => {
    const next = saved.filter((_, i) => i !== index)
    setSaved(next)
    saveViewpoints(next)
  }

  return (
    <>
      <RainCanvas map={map} rain={lite ? 0 : effect.rain} snow={lite ? 0 : effect.snow} />
      {open && (
        <div className="twin-panel" role="group" aria-label="Digital twin" onKeyDown={(e) => { if (e.key === "Escape") onClose() }}>
          <div className="twin-head">
            <strong>Digital twin</strong>
            <button type="button" aria-label="Close the digital twin panel" onClick={onClose}>×</button>
          </div>

          <section aria-label="Light">
            <h4>Light <span>{hour === null ? `Now, ${ukClock(date)}` : `${ukClock(date)} today`} · sun {sun.altitude.toFixed(0)}°</span></h4>
            <input type="range" aria-label="Time of day" min={0} max={23.75} step={0.25}
              value={hour ?? Math.round(ukHour(date) * 4) / 4}
              onChange={(e) => setHour(Number(e.target.value))} />
            {hour !== null && <button type="button" className="twin-link" onClick={() => setHour(null)}>Back to now</button>}
          </section>

          <section aria-label="Weather">
            <label className="twin-check">
              <input type="checkbox" checked={weatherOn} onChange={(e) => setWeatherOn(e.target.checked)} />
              Live weather
            </label>
            <p className="twin-note">
              {!weatherOn ? "Off: a clear sky." : weatherError ? weatherError : weather
                ? `${effect.label}. Open-Meteo, ${weather.time.slice(11)}. Shown on the map only; traffic is not affected.`
                : "Loading…"}
            </p>
          </section>

          <section aria-label="Map layers">
            <h4>Layers</h4>
            <label className="twin-check">
              <input type="checkbox" checked={terrain && !lite} disabled={lite} onChange={(e) => setTerrain(e.target.checked)} />
              Terrain and hillshade
            </label>
            <label className="twin-check">
              <input type="checkbox" checked={show.population} onChange={(e) => setShow((s) => ({ ...s, population: e.target.checked }))} />
              Population density (Census 2021)
            </label>
            <label className="twin-check">
              <input type="checkbox" checked={show.coverage} onChange={(e) => setShow((s) => ({ ...s, coverage: e.target.checked }))} />
              Emergency coverage, existing
              <select aria-label="Coverage service" value={service} onChange={(e) => setService(e.target.value as "fire" | "police")}>
                <option value="fire">fire</option>
                <option value="police">police</option>
              </select>
            </label>
            <label className="twin-check">
              <input type="checkbox" checked={show.congestion} onChange={(e) => setShow((s) => ({ ...s, congestion: e.target.checked }))} />
              Congestion in 3D
            </label>
            {planError && <p className="twin-note twin-error">{planError}</p>}
            {show.coverage && <p className="twin-note">Road-network response time from OpenStreetMap's {service} stations; red outlines: 50+ residents beyond the target.</p>}
          </section>

          <section aria-label="Viewpoints">
            <h4>Viewpoints</h4>
            <div className="twin-views">
              {VIEWPOINTS.map((view) => <button key={view.name} type="button" onClick={() => go(view)}>{view.name}</button>)}
              {saved.map((view, i) => (
                <span key={`${view.name}-${i}`} className="twin-saved">
                  <button type="button" onClick={() => go(view)}>{view.name}</button>
                  <button type="button" aria-label={`Forget ${view.name}`} onClick={() => forget(i)}>×</button>
                </span>
              ))}
            </div>
            <div className="twin-actions">
              <button type="button" onClick={saveHere}>Save this view</button>
              <button type="button" aria-pressed={touring} onClick={() => setTouring(!touring)}>{touring ? "Stop fly-over" : "Fly-over"}</button>
            </div>
          </section>

          <section aria-label="Performance">
            <label className="twin-check">
              <input type="checkbox" checked={lite} onChange={(e) => setLite(e.target.checked)} />
              Performance mode
            </label>
            <p className="twin-note">Standard resolution, no terrain or weather effects{preferLite(deviceProfile(), vehicles) ? "; suggested for this device" : ""}.</p>
          </section>
        </div>
      )}
    </>
  )
}

// Rain or snow over the map, as heavy as Open-Meteo's reading: falling
// streaks drawn on a canvas that lets clicks through
function RainCanvas({ map, rain, snow }: { map: MapLibreMap | null; rain: number; snow: number }) {
  const canvas = useRef<HTMLCanvasElement | null>(null)
  useEffect(() => {
    if (!map) return
    const element = document.createElement("canvas")
    element.className = "twin-rain"
    element.setAttribute("aria-hidden", "true")
    map.getContainer().appendChild(element)
    canvas.current = element
    return () => { element.remove(); canvas.current = null }
  }, [map])

  useEffect(() => {
    const element = canvas.current
    if (!element || (rain <= 0 && snow <= 0)) {
      element?.getContext("2d")?.clearRect(0, 0, element.width, element.height)
      return
    }
    const context = element.getContext("2d")
    if (!context) return
    const count = Math.round(80 + 520 * Math.max(rain, snow))
    let drops: { x: number; y: number; speed: number }[] = []
    let frame = 0
    const resize = () => {
      element.width = element.clientWidth
      element.height = element.clientHeight
      drops = Array.from({ length: count }, () => ({
        x: Math.random() * element.width, y: Math.random() * element.height, speed: 0.6 + Math.random() * 0.8,
      }))
    }
    resize()
    const tick = () => {
      context.clearRect(0, 0, element.width, element.height)
      context.strokeStyle = snow > rain ? "rgba(235, 242, 250, 0.7)" : "rgba(170, 196, 224, 0.45)"
      context.lineWidth = snow > rain ? 2 : 1
      context.beginPath()
      for (const drop of drops) {
        const fall = snow > rain ? 1.2 * drop.speed : 9 * drop.speed
        drop.y += fall
        drop.x += snow > rain ? Math.sin(drop.y / 30) * 0.4 : -1.2 * drop.speed
        if (drop.y > element.height) { drop.y = -10; drop.x = Math.random() * element.width }
        context.moveTo(drop.x, drop.y)
        context.lineTo(drop.x + (snow > rain ? 0.5 : -2), drop.y + (snow > rain ? 1 : 9))
      }
      context.stroke()
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    window.addEventListener("resize", resize)
    return () => { cancelAnimationFrame(frame); window.removeEventListener("resize", resize) }
  }, [rain, snow, map])
  return null
}
