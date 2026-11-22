// The 3D map: MapLibre GL with OpenFreeMap's vector tiles and extruded
// buildings, drawing the same simulation state as the 2D map.
//
// Loaded on demand (React.lazy in App.tsx), so the 2D map never downloads
// MapLibre. OpenFreeMap needs no API key, so there is no secret to keep.

import { useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import maplibregl, {
  type GeoJSONSource,
  type Map as MapLibreMap,
  type MapLayerMouseEvent,
} from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"

import { COLOURS, LINE, MARKER, VEHICLE_3D } from "./mapStyles.ts"
import { OPS_STYLE, buildingsLayer } from "./mapTheme.ts"
import { VehicleGlide } from "./vehicles3d.ts"
import { TwinPanel } from "./TwinPanel.tsx"
import {
  SOURCES,
  describeBuilding,
  describeRoad,
  describeSegment,
  sameSegment,
  describeVehicle,
  type FeatureCollection,
  type InspectedRoad,
  type SourceId,
} from "./mapData.ts"
import { request } from "./scenarios.ts"
import type { Coordinate } from "./simulationHelpers.ts"

// A clean, light basemap; the simulation layers carry the colour
// Our own dark operations style from OpenFreeMap's vector tiles (mapTheme.ts)
const STYLE = OPS_STYLE



// Looking north-west over the city centre, the castle and the cathedral
const NORWICH_VIEW = {
  center: [1.2965, 52.6295] as [number, number],
  zoom: 14.6,
  pitch: 55,
  bearing: -17,
}

const FONT = ["Noto Sans Bold"]
const STATION_LAYERS = ["stations-circle", "stations-label"]
const VEHICLE_LAYERS = ["emergency-vehicles", "vehicle-bodies", "vehicles"]
const ROAD_LAYERS = ["congestion", "routes", "routes-emergency", "closures", "hotspots"]

// What the Layers menu can switch off, and the map layers behind each
const LAYER_GROUPS = [
  { key: "buildings", label: "3D buildings", layers: ["buildings-3d"] },
  { key: "traffic", label: "Live congestion", layers: ["congestion"] },
  { key: "vehicles", label: "Vehicles", layers: ["vehicles", "vehicle-bodies", "emergency-vehicles", "emergency-vehicles-label"] },
  { key: "routes", label: "Routes and closures", layers: ["routes", "routes-emergency", "closures"] },
  { key: "signals", label: "Traffic signals", layers: ["signals", "signals-label"] },
  { key: "floods", label: "Floods", layers: ["floods"] },
  { key: "analysis", label: "Stations and scenario results", layers: [...STATION_LAYERS, "hotspots"] },
] as const

type LayerKey = (typeof LAYER_GROUPS)[number]["key"]
const HIDDEN_KEY = "norwich-sim-3d-hidden-layers"

function savedHidden(): Set<LayerKey> {
  try {
    const saved = JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? "[]")
    const known = new Set<string>(LAYER_GROUPS.map((group) => group.key))
    return new Set(Array.isArray(saved) ? saved.filter((key) => known.has(key)) : [])
  } catch {
    return new Set()
  }
}

type Props = {
  data: Record<SourceId, FeatureCollection>
  onClick: (location: Coordinate) => void
  // WebGL missing or the map failed to start: the app falls back to 2D
  onFailed: (reason: string) => void
  // Inspect mode: clicks describe what is there instead of placing points
  inspecting: boolean
  onInspectingChange: (inspecting: boolean) => void
  // Where the camera goes instead of following new emergencies, stations
  // and routes (the command centre): it moves when the key changes
  focus?: CameraFocus | null
}

export type CameraFocus = { key: string; positions: [number, number][]; maxZoom?: number }

type Position = [number, number]

// Where the camera should go when something new appears: the newest
// emergency, a new set of stations, or a newly planned route. Each has a
// key that changes only when the thing itself does (a diversion or an
// ambulance re-planning around a closure does not move the camera).
function cameraTargets(data: Props["data"]) {
  const routes = data.routes.features
  const line = (kind: string) =>
    routes.find((feature) => feature.properties.kind === kind)?.geometry.coordinates as Position[] | undefined
  const route = line("route")
  const emergency = line("emergency")
  const incident = data.points.features.find((feature) => feature.properties.kind === "incident")
  const stations = data.stations.features.map((feature) => feature.geometry.coordinates as Position)

  return [
    {
      id: "emergency",
      key: emergency ? String(emergency[emergency.length - 1]) : "",
      positions: [...(emergency ?? []), ...(incident ? [incident.geometry.coordinates as Position] : [])],
    },
    { id: "stations", key: stations.map(String).join(";"), positions: stations },
    { id: "route", key: route ? `${route[0]};${route[route.length - 1]}` : "", positions: route ?? [] },
  ]
}

function addLayers(map: MapLibreMap) {
  for (const id of [...SOURCES, "inspected"]) {
    map.addSource(id, { type: "geojson", data: { type: "FeatureCollection", features: [] } })
  }

  // The basemap's labels sit on top of everything we add below them
  const firstLabel = map.getStyle().layers.find((layer) => layer.type === "symbol")?.id

  // Ground level: congestion, scenario hotspots, closures and routes. Drawn
  // before the buildings so buildings in front of a road hide it, as in 3D.
  map.addLayer({
    id: "hotspots", type: "line", source: "hotspots",
    layout: { "line-cap": "round" },
    paint: { "line-color": COLOURS.delayHotspot, "line-width": LINE.delayHotspot, "line-opacity": 0.55 },
  }, firstLabel)
  // Congestion along each road's real shape: a soft glow under a firm line,
  // wider as you zoom in
  map.addLayer({
    id: "congestion-glow", type: "line", source: "congestion",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["get", "colour"],
      "line-width": ["interpolate", ["linear"], ["zoom"], 11, 4, 15, 12, 18, 30],
      // Only congestion glows: free-flowing traffic stays quiet
      "line-opacity": ["match", ["get", "level"], "light", 0, 0.28],
      "line-blur": 4,
    },
  }, firstLabel)
  map.addLayer({
    id: "congestion", type: "line", source: "congestion",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["get", "colour"],
      "line-width": ["interpolate", ["linear"], ["zoom"], 11, 1.5, 15, 4, 18, 10],
      "line-opacity": ["match", ["get", "level"], "light", 0.35, 0.95],
    },
  }, firstLabel)
  map.addLayer({
    id: "closures", type: "line", source: "closures",
    paint: {
      "line-color": COLOURS.closure,
      "line-width": LINE.closure,
      "line-opacity": 0.9,
      "line-dasharray": ["case", ["==", ["get", "kind"], "scenario"], ["literal", [1, 1]], ["literal", [1, 0]]],
    },
  }, firstLabel)
  map.addLayer({
    id: "floods", type: "line", source: "floods",
    layout: { "line-cap": "round" },
    paint: { "line-color": COLOURS.flood, "line-width": LINE.closure + 2, "line-opacity": ["get", "opacity"] },
  }, firstLabel)
  map.addLayer({
    id: "routes", type: "line", source: "routes",
    filter: ["!=", ["get", "kind"], "emergency"],
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": ["get", "colour"], "line-width": LINE.route + 1, "line-opacity": 0.95 },
  }, firstLabel)
  map.addLayer({
    id: "routes-emergency", type: "line", source: "routes",
    filter: ["==", ["get", "kind"], "emergency"],
    paint: { "line-color": ["coalesce", ["get", "colour"], COLOURS.emergency], "line-width": LINE.emergency + 1, "line-dasharray": [2, 1.5] },
  }, firstLabel)
  // The road being inspected: a dark line with a white keyline
  map.addLayer({
    id: "inspected-casing", type: "line", source: "inspected",
    layout: { "line-cap": "round" },
    paint: { "line-color": "#ffffff", "line-width": 11 },
  }, firstLabel)
  map.addLayer({
    id: "inspected", type: "line", source: "inspected",
    layout: { "line-cap": "round" },
    paint: { "line-color": COLOURS.closure, "line-width": 6 },
  }, firstLabel)

  // Buildings, extruded to their mapped height (OpenMapTiles' render_height:
  // the OSM height, or floors x 3.66 m, or a default when neither is mapped)
  map.addLayer(buildingsLayer(), firstLabel)

  // Vehicles as 3D bodies from zoom 15 (vehicles3d.ts), extruded in one
  // batched layer: understated grey for ordinary traffic, the service's
  // colour for emergency vehicles
  map.addSource("vehicle-bodies", { type: "geojson", data: { type: "FeatureCollection", features: [] } })
  map.addLayer({
    id: "vehicle-bodies", type: "fill-extrusion", source: "vehicle-bodies",
    minzoom: 15,
    paint: {
      "fill-extrusion-color": ["case", ["get", "emergency"], ["get", "colour"], VEHICLE_3D],
      "fill-extrusion-height": ["get", "height"],
      "fill-extrusion-base": 0.2,
      "fill-extrusion-opacity": 1,
    },
  }, firstLabel)

  // Above the buildings, so they are never hidden: points, stations, vehicles
  map.addLayer({
    id: "points", type: "circle", source: "points",
    paint: {
      "circle-color": ["get", "colour"],
      "circle-opacity": ["case", ["==", ["get", "kind"], "incident"], 0.18, 1],
      "circle-radius": ["case", ["==", ["get", "kind"], "incident"], MARKER.incident + 2, MARKER.point + 1],
      "circle-stroke-color": ["case", ["==", ["get", "kind"], "incident"], ["coalesce", ["get", "colour"], COLOURS.emergency], MARKER.outline],
      "circle-stroke-width": 2.5,
      "circle-pitch-alignment": "viewport",
    },
  })
  // A point's label, when it has one (the command centre's incidents)
  map.addLayer({
    id: "points-label", type: "symbol", source: "points",
    filter: ["has", "label"],
    layout: {
      "text-field": ["get", "label"], "text-font": FONT, "text-size": 11,
      "text-offset": [0, 1.9], "text-allow-overlap": true,
    },
    paint: { "text-color": "#ffffff", "text-halo-color": "#0b1016", "text-halo-width": 1.4 },
  })
  // The overview: a small dot per vehicle until the bodies take over
  map.addLayer({
    id: "vehicles", type: "circle", source: "vehicles",
    filter: ["!", ["get", "emergency"]],
    maxzoom: 15.5,
    paint: {
      "circle-color": VEHICLE_3D,
      // Small when zoomed out, where thousands of vehicles share the screen
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 1.4, 14.5, 2.4, 15.5, 3],
      "circle-opacity": ["interpolate", ["linear"], ["zoom"], 15, 0.9, 15.5, 0],
    },
  })
  map.addLayer({
    id: "emergency-vehicles", type: "circle", source: "vehicles",
    filter: ["get", "emergency"],
    paint: {
      "circle-color": ["get", "colour"],
      "circle-radius": MARKER.ambulance + 3,
      "circle-stroke-color": MARKER.outline,
      "circle-stroke-width": 2.5,
    },
  })
  map.addLayer({
    id: "emergency-vehicles-label", type: "symbol", source: "vehicles",
    filter: ["get", "emergency"],
    layout: {
      "text-field": ["get", "letter"], "text-font": FONT, "text-size": 11,
      "text-allow-overlap": true, "text-ignore-placement": true,
    },
    paint: { "text-color": "#ffffff" },
  })
  // Traffic signals: each approach's light, bigger with a queue behind it
  map.addLayer({
    id: "signals", type: "circle", source: "signals",
    paint: {
      "circle-color": ["get", "colour"],
      "circle-radius": ["+", 4, ["min", 6, ["*", 0.6, ["get", "queue"]]]],
      "circle-stroke-color": MARKER.outline,
      "circle-stroke-width": 1.5,
    },
  })
  map.addLayer({
    id: "signals-label", type: "symbol", source: "signals",
    layout: {
      "text-field": ["get", "label"], "text-font": FONT, "text-size": 10,
      "text-allow-overlap": true, "text-ignore-placement": true,
    },
    paint: { "text-color": "#ffffff" },
  })
  map.addLayer({
    id: "stations-circle", type: "circle", source: "stations",
    paint: {
      "circle-color": ["get", "colour"],
      "circle-radius": ["case", ["==", ["get", "kind"], "station"], MARKER.chosenStation + 1, MARKER.candidate],
      "circle-stroke-color": MARKER.outline,
      "circle-stroke-width": 2.5,
    },
  })
  map.addLayer({
    id: "stations-label", type: "symbol", source: "stations",
    layout: {
      "text-field": ["get", "label"], "text-font": FONT, "text-size": 12,
      "text-allow-overlap": true, "text-ignore-placement": true,
    },
    paint: { "text-color": "#ffffff" },
  })
}

function moveCamera(map: MapLibreMap, data: Props["data"], focused: Map<string, string>, focus?: CameraFocus | null) {
  let target: Position[] | null = null
  const maxZoom = focus?.maxZoom ?? 16.5
  const targets = focus === undefined ? cameraTargets(data) : focus ? [{ id: "focus", ...focus }] : []
  for (const { id, key, positions } of targets) {
    if (focused.get(id) === key) continue
    focused.set(id, key)
    // The first new thing in the list wins (emergency, stations, route)
    if (key && positions.length > 0 && !target) target = positions
  }
  if (!target) return

  const bounds = new maplibregl.LngLatBounds(target[0], target[0])
  for (const position of target) bounds.extend(position)
  const { clientWidth: width, clientHeight: height } = map.getContainer()
  // Room for the legend at the bottom; MapLibre animates unless the
  // visitor prefers reduced motion
  map.fitBounds(bounds, {
    padding: {
      top: Math.min(80, height * 0.15),
      bottom: Math.min(150, height * 0.3),
      left: Math.min(70, width * 0.12),
      right: Math.min(70, width * 0.12),
    },
    maxZoom,
    pitch: map.getPitch(),
    bearing: map.getBearing(),
    duration: 1400,
  })
}

function applyVisibility(map: MapLibreMap, hidden: Set<LayerKey>) {
  for (const group of LAYER_GROUPS) {
    for (const layer of group.layers) {
      map.setLayoutProperty(layer, "visibility", hidden.has(group.key) ? "none" : "visible")
    }
  }
}

// Popup text: the first line is the title
function popupContent(lines: string[]) {
  const content = document.createElement("div")
  content.className = "map-3d-popup"
  lines.forEach((line, index) => {
    const element = document.createElement(index === 0 ? "strong" : "div")
    element.textContent = line
    content.appendChild(element)
  })
  return content
}

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] }

export default function Map3D({ data, onClick, onFailed, inspecting, onInspectingChange, focus }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const popupRef = useRef<maplibregl.Popup | null>(null)
  const ready = useRef(false)
  // What each source last received, so unchanged sources are not re-sent
  const sent = useRef(new Map<SourceId, FeatureCollection>())
  // What the camera last moved to (see cameraTargets)
  const focused = useRef(new Map<string, string>())
  // The newest props, for MapLibre's event handlers (set up once)
  const latest = useRef({ data, onClick, onFailed, inspecting, focus })
  useEffect(() => {
    latest.current = { data, onClick, onFailed, inspecting, focus }
  })

  const [hidden, setHidden] = useState(savedHidden)
  const [layersOpen, setLayersOpen] = useState(false)
  const [twinOpen, setTwinOpen] = useState(false)
  // The map once it has loaded (the digital twin panel adds its layers then)
  const [loaded, setLoaded] = useState<MapLibreMap | null>(null)
  // The map's own control corner holds our buttons, under the zoom and
  // compass, so MapLibre lays them out with its controls
  const [toolsHost] = useState(() => {
    const element = document.createElement("div")
    element.className = "maplibregl-ctrl map-3d-tools"
    return element
  })

  // Vehicles glide from where they are drawn to their new positions
  const glide = useRef(new VehicleGlide())

  function push() {
    const map = mapRef.current
    if (!map || !ready.current) return
    for (const id of SOURCES) {
      const collection = latest.current.data[id]
      if (sent.current.get(id) === collection) continue
      if (id === "vehicles") glide.current.set(map, collection)
      else (map.getSource(id) as GeoJSONSource | undefined)?.setData(collection)
      sent.current.set(id, collection)
    }
    moveCamera(map, latest.current.data, focused.current, latest.current.focus)
  }

  useEffect(() => {
    if (!container.current) return
    let map: MapLibreMap
    try {
      map = new maplibregl.Map({
        container: container.current,
        style: STYLE,
        ...NORWICH_VIEW,
        maxPitch: 70,
        attributionControl: { compact: true },
      })
    } catch (error) {
      // No WebGL (an old browser, or graphics switched off)
      latest.current.onFailed(error instanceof Error ? error.message : String(error))
      return
    }
    mapRef.current = map
    // Lets the end-to-end tests ask the map what it has drawn
    Object.defineProperty(container.current, "maplibre", { value: map, configurable: true })
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left")
    map.addControl({ onAdd: () => toolsHost, onRemove: () => toolsHost.remove() }, "top-left")
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right")
    // Fullscreen takes the map with its legend and messages
    map.addControl(
      new maplibregl.FullscreenControl({ container: container.current.parentElement ?? undefined }),
      "top-left",
    )
    // Bodies are sized for the zoom (bodyScale): redraw when it changes
    map.on("zoomend", () => {
      if (ready.current) glide.current.draw(map, performance.now())
    })

    const idleCursor = () => (latest.current.inspecting ? "help" : "crosshair")
    map.getCanvas().style.cursor = idleCursor()

    map.on("load", () => {
      addLayers(map)
      applyVisibility(map, savedHidden())
      ready.current = true
      setLoaded(map)
      push()
      // On a phone the open credit line covers the legend: start it folded
      // into MapLibre's "i" button, one tap away
      if (map.getContainer().clientWidth < 640) {
        map.getContainer().querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show")
      }
      container.current?.setAttribute("data-ready", "true")
    })

    // Closed by us (each click replaces it) or by its close button
    const popup = new maplibregl.Popup({ closeOnClick: false, offset: 14, maxWidth: "260px" })
    popupRef.current = popup
    const highlight = (road: FeatureCollection) =>
      (map.getSource("inspected") as GeoJSONSource | undefined)?.setData(road)
    popup.on("close", () => highlight(EMPTY))
    const show = (event: MapLayerMouseEvent, lines: string[]) =>
      popup.setLngLat(event.lngLat).setDOMContent(popupContent(lines)).addTo(map)

    // A road lookup still running when the next click comes is ignored
    let lookup = 0

    map.on("click", async (event: MapLayerMouseEvent) => {
      popup.remove()
      setLayersOpen(false)
      if (!ready.current) return
      const { x, y } = event.point
      // A few pixels of slack: vehicles are small
      const near: [[number, number], [number, number]] = [[x - 4, y - 4], [x + 4, y + 4]]

      // A station marker always shows its numbers
      const station = map.queryRenderedFeatures(event.point, { layers: STATION_LAYERS })[0]
      if (station) {
        show(event, String(station.properties?.popup ?? "").split("\n"))
        return
      }
      if (!latest.current.inspecting) {
        latest.current.onClick([event.lngLat.lat, event.lngLat.lng])
        return
      }

      const vehicle = map.queryRenderedFeatures(near, { layers: VEHICLE_LAYERS })[0]
      if (vehicle) {
        show(event, describeVehicle(vehicle.properties))
        return
      }
      // A road the simulation is drawing (traffic, a route, a closure) wins
      // over a building in front of it: that road is what the visitor sees
      const simulatedRoad = map.queryRenderedFeatures(near, { layers: ROAD_LAYERS })
      const building = simulatedRoad.length > 0
        ? undefined
        : map.queryRenderedFeatures(event.point, { layers: ["buildings-3d"] })[0]
      if (building) {
        const number = (value: unknown) => (typeof value === "number" ? value : undefined)
        show(event, describeBuilding(number(building.properties.render_height), number(building.properties.render_min_height)))
        return
      }

      // Anything else: the simulation's nearest road, with its live traffic.
      // On a congestion segment, ask about a point a tenth of the way along
      // it: the nearest junction is then its own end, and the road through
      // the point is the segment itself, not a side road near the click.
      const segment = simulatedRoad.find((feature) => feature.layer.id === "congestion")
      // Its two junctions: the ends of the line (drawn along the road's shape)
      const coordinates = segment?.geometry.type === "LineString" ? (segment.geometry.coordinates as Position[]) : []
      const ends = coordinates.length >= 2 ? [coordinates[0], coordinates[coordinates.length - 1]] : null
      const [longitude, latitude] = ends
        ? [ends[0][0] + (ends[1][0] - ends[0][0]) * 0.1, ends[0][1] + (ends[1][1] - ends[0][1]) * 0.1]
        : [event.lngLat.lng, event.lngLat.lat]
      const mine = ++lookup
      show(event, ["Road", "Finding the nearest road…"])
      try {
        const road = await request<InspectedRoad>(
          `/scenarios/nearest-road?latitude=${latitude}&longitude=${longitude}`,
        )
        if (mine !== lookup || !popup.isOpen()) return
        const roadEnds: Position[] = [
          [road.start_point.longitude, road.start_point.latitude],
          [road.end_point.longitude, road.end_point.latitude],
        ]
        if (segment && ends && !sameSegment(ends, roadEnds)) {
          // The lookup found a different road: say only what the map knows
          show(event, describeSegment({ type: "Feature", geometry: { type: "LineString", coordinates: ends }, properties: segment.properties }))
          return
        }
        show(event, describeRoad(road, latest.current.data.congestion))
        highlight({
          type: "FeatureCollection",
          features: [{
            type: "Feature",
            geometry: {
              type: "LineString",
              coordinates: roadEnds,
            },
            properties: {},
          }],
        })
      } catch (error) {
        if (mine !== lookup || !popup.isOpen()) return
        show(event, ["Road", error instanceof Error ? error.message : "The road could not be found"])
      }
    })
    for (const layer of [...STATION_LAYERS, ...VEHICLE_LAYERS]) {
      map.on("mouseenter", layer, () => {
        if (STATION_LAYERS.includes(layer) || latest.current.inspecting) {
          map.getCanvas().style.cursor = "pointer"
        }
      })
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = idleCursor() })
    }

    const sentSources = sent.current
    const focusedTargets = focused.current
    const vehicles = glide.current
    return () => {
      vehicles.stop()
      ready.current = false
      sentSources.clear()
      focusedTargets.clear()
      mapRef.current = null
      popupRef.current = null
      setLoaded(null)
      // Frees the WebGL context and its GPU memory
      map.remove()
    }
  }, [toolsHost])

  useEffect(push, [data, focus])

  // Leaving inspect mode closes what it was showing
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    map.getCanvas().style.cursor = inspecting ? "help" : "crosshair"
    if (!inspecting) popupRef.current?.remove()
  }, [inspecting])

  useEffect(() => {
    try {
      localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hidden]))
    } catch {
      // Private browsing: the choice lasts for this visit only
    }
    if (mapRef.current && ready.current) applyVisibility(mapRef.current, hidden)
  }, [hidden])

  function toggleLayer(key: LayerKey) {
    setHidden((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function resetView() {
    popupRef.current?.remove()
    mapRef.current?.easeTo({ ...NORWICH_VIEW, duration: 1200 })
  }

  const tools = (
    <>
      <div className="maplibregl-ctrl-group">
        <button
          type="button"
          title="Layers"
          aria-label="Layers"
          aria-expanded={layersOpen}
          aria-controls="map-3d-layers"
          onClick={() => setLayersOpen((open) => !open)}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M10 3 2.5 7 10 11l7.5-4L10 3Z" />
            <path d="m2.5 10.5 7.5 4 7.5-4M2.5 14l7.5 4 7.5-4" />
          </svg>
        </button>
        <button
          type="button"
          title="Inspect: click a road, building or vehicle"
          aria-label="Inspect"
          aria-pressed={inspecting}
          onClick={() => onInspectingChange(!inspecting)}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="10" cy="10" r="7" />
            <path d="M10 9v5M10 6.2v.1" />
          </svg>
        </button>
        <button
          type="button"
          title="Digital twin: light, weather, terrain, layers and viewpoints"
          aria-label="Digital twin"
          aria-expanded={twinOpen}
          onClick={() => { setTwinOpen((open) => !open); setLayersOpen(false) }}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="10" cy="10" r="3.2" />
            <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4" />
          </svg>
        </button>
        <button type="button" title="Back to the starting view" aria-label="Reset view" onClick={resetView}>
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M3.5 9.5 10 4l6.5 5.5M5.5 8v8h9V8" />
          </svg>
        </button>
      </div>
      {layersOpen && (
        <div
          className="map-3d-layers"
          id="map-3d-layers"
          role="group"
          aria-label="Layers"
          onKeyDown={(event) => {
            if (event.key === "Escape") setLayersOpen(false)
          }}
        >
          <strong>Layers</strong>
          {LAYER_GROUPS.map((group) => (
            <label key={group.key}>
              <input
                type="checkbox"
                checked={!hidden.has(group.key)}
                onChange={() => toggleLayer(group.key)}
              />
              {group.label}
            </label>
          ))}
        </div>
      )}
    </>
  )

  return (
    <>
      <div ref={container} className="map map-3d" data-testid="map-3d" />
      <TwinPanel
        map={loaded}
        congestion={data.congestion}
        vehicles={data.vehicles.features.length}
        open={twinOpen}
        onClose={() => setTwinOpen(false)}
      />
      {createPortal(tools, toolsHost)}
    </>
  )
}
