// The planning view's 3D map (MapLibre, the same dark style and buildings
// as the 3D traffic map). Loaded on demand. It draws what it is given:
// coverage hexagons, station columns and labels (planning.ts builds them
// from the backend's calculation), and morphs the hexagons from one
// layout's real values to the next.

import { useEffect, useRef } from "react"
import maplibregl from "./maplibre.ts"
import { type Map as MapLibreMap } from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"

import type { FeatureCollection } from "./mapData.ts"
import { OPS_STYLE, buildingsLayer } from "./mapTheme.ts"
import { coverageFeatures, interpolateSeconds, type Plan } from "./planning.ts"
import { PlanningLayers, type PlanningMap } from "./planningLayers.ts"

export type Visible = { coverage: boolean; underserved: boolean; stations: boolean; buildings: boolean }

type Props = {
  plan: Plan | null
  // Coverage to show: response seconds per hexagon (morphed to), or a
  // ready-made collection (the difference view, drawn as it is)
  seconds: (number | null)[] | null
  beyond: number[]
  fixed: FeatureCollection | null
  columns: FeatureCollection
  labels: FeatureCollection
  visible: Visible
  threeD: boolean
  // Each change turns the camera a little (the animation's steps)
  turn: number
  onFailed: (reason: string) => void
}

// Zoom 13 and above: the map tiles have no buildings below it
const VIEW = { center: [1.2935, 52.6305] as [number, number], zoom: 13.1, pitch: 58, bearing: -20 }
const MORPH_MS = 900

export default function PlanningCanvas(props: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const layersRef = useRef<PlanningLayers | null>(null)
  const latest = useRef(props)
  const shown = useRef<(number | null)[] | null>(null)
  const frame = useRef(0)
  useEffect(() => {
    latest.current = props
  })

  // Draw the current props (called on load and on every change)
  function draw(morph: boolean) {
    const layers = layersRef.current
    const map = mapRef.current
    const p = latest.current
    if (!layers || !map) return
    layers.update("plan-columns", p.columns)
    layers.update("plan-labels", p.labels)
    layers.setGroup("coverage", p.visible.coverage, p.threeD)
    layers.setGroup("underserved", p.visible.underserved)
    layers.setGroup("stations", p.visible.stations)
    if (map.getLayer("buildings-3d")) {
      map.setLayoutProperty("buildings-3d", "visibility", p.visible.buildings ? "visible" : "none")
    }
    cancelAnimationFrame(frame.current)
    if (p.fixed || !p.plan || !p.seconds) {
      layers.update("plan-coverage", p.fixed ?? { type: "FeatureCollection", features: [] })
      shown.current = null
      return
    }
    const plan = p.plan
    const target = p.seconds
    const from = shown.current
    if (!morph || !from || from.length !== target.length) {
      layers.update("plan-coverage", coverageFeatures(plan, target, p.beyond))
      shown.current = target
      return
    }
    // Morph from what is drawn to the new values: both are real results
    const start = performance.now()
    const tick = (time: number) => {
      const t = Math.min(1, (time - start) / MORPH_MS)
      const eased = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
      const values = interpolateSeconds(from, target, eased)
      layers.update("plan-coverage", coverageFeatures(plan, values, p.beyond))
      shown.current = values
      if (t < 1) frame.current = requestAnimationFrame(tick)
    }
    frame.current = requestAnimationFrame(tick)
  }

  useEffect(() => {
    if (!container.current) return
    let map: MapLibreMap
    try {
      map = new maplibregl.Map({
        container: container.current, style: OPS_STYLE, ...VIEW, maxPitch: 75,
        attributionControl: { compact: true },
      })
    } catch (error) {
      latest.current.onFailed(error instanceof Error ? error.message : String(error))
      return
    }
    mapRef.current = map
    Object.defineProperty(container.current, "maplibre", { value: map, configurable: true })
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left")
    map.addControl(new maplibregl.FullscreenControl({ container: container.current.parentElement ?? undefined }), "top-left")
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right")
    map.on("load", () => {
      const firstLabel = map.getStyle().layers.find((layer) => layer.type === "symbol")?.id
      map.addLayer(buildingsLayer(0.8), firstLabel)
      const layers = new PlanningLayers(map as unknown as PlanningMap, firstLabel)
      layers.create()
      layersRef.current = layers
      draw(false)
      container.current?.setAttribute("data-ready", "true")
    })
    return () => {
      cancelAnimationFrame(frame.current)
      layersRef.current = null
      mapRef.current = null
      map.remove()
    }
  }, [])

  // New data: morph the coverage when only the values changed
  useEffect(() => {
    draw(true)
  }, [props.plan, props.seconds, props.fixed, props.columns, props.labels, props.visible, props.threeD, props.beyond])

  // A step of the animation: turn the camera a little
  useEffect(() => {
    const map = mapRef.current
    if (!map || props.turn === 0) return
    map.easeTo({ bearing: map.getBearing() + 9, duration: 1600 })
  }, [props.turn])

  return <div ref={container} className="map planning-map" data-testid="planning-map" />
}
