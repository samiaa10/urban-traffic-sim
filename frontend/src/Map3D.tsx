// The 3D map: MapLibre GL with OpenFreeMap's vector tiles and extruded
// buildings, drawing the same simulation state as the 2D map.
//
// Loaded on demand (React.lazy in App.tsx), so the 2D map never downloads
// MapLibre. OpenFreeMap needs no API key, so there is no secret to keep.

import { useEffect, useRef } from "react"
import maplibregl, {
  type ExpressionSpecification,
  type GeoJSONSource,
  type Map as MapLibreMap,
  type MapLayerMouseEvent,
} from "maplibre-gl"
import "maplibre-gl/dist/maplibre-gl.css"

import { COLOURS, LINE, MARKER } from "./mapStyles.ts"
import { SOURCES, type FeatureCollection, type SourceId } from "./mapData.ts"
import type { Coordinate } from "./simulationHelpers.ts"

// A clean, light basemap; the simulation layers carry the colour
const STYLE = "https://tiles.openfreemap.org/styles/positron"

// Looking north-west over the city centre, the castle and the cathedral
const NORWICH_VIEW = {
  center: [1.2965, 52.6295] as [number, number],
  zoom: 14.6,
  pitch: 55,
  bearing: -17,
}

const FONT = ["Noto Sans Bold"]
// A few buildings come with no height at all; treat them as flat
const HEIGHT: ExpressionSpecification = ["coalesce", ["get", "render_height"], 0]
const STATION_LAYERS = ["stations-circle", "stations-label"]

type Props = {
  data: Record<SourceId, FeatureCollection>
  onClick: (location: Coordinate) => void
  // WebGL missing or the map failed to start: the app falls back to 2D
  onFailed: (reason: string) => void
}

function addLayers(map: MapLibreMap) {
  for (const id of SOURCES) {
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
  map.addLayer({
    id: "congestion", type: "line", source: "congestion",
    paint: { "line-color": ["get", "colour"], "line-width": LINE.congestion, "line-opacity": 0.6 },
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
    id: "routes", type: "line", source: "routes",
    filter: ["!=", ["get", "kind"], "emergency"],
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": ["get", "colour"], "line-width": LINE.route + 1, "line-opacity": 0.95 },
  }, firstLabel)
  map.addLayer({
    id: "routes-emergency", type: "line", source: "routes",
    filter: ["==", ["get", "kind"], "emergency"],
    paint: { "line-color": COLOURS.emergency, "line-width": LINE.emergency + 1, "line-dasharray": [2, 1.5] },
  }, firstLabel)

  // Buildings, extruded to their mapped height (OpenMapTiles' render_height:
  // the OSM height, or floors x 3.66 m, or a default when neither is mapped)
  map.addLayer({
    id: "buildings-3d", type: "fill-extrusion", source: "openmaptiles", "source-layer": "building",
    minzoom: 13,
    filter: ["!", ["has", "hide_3d"]],
    paint: {
      // Taller buildings a little darker, in the app's slate blues
      "fill-extrusion-color": [
        "interpolate", ["linear"], HEIGHT,
        0, "#d3dae2", 15, "#bcc6d1", 40, "#9eabb9",
      ],
      "fill-extrusion-height": ["interpolate", ["linear"], ["zoom"], 13, 0, 14.5, HEIGHT],
      "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
      "fill-extrusion-opacity": 0.82,
    },
  }, firstLabel)

  // Above the buildings, so they are never hidden: points, stations, vehicles
  map.addLayer({
    id: "points", type: "circle", source: "points",
    paint: {
      "circle-color": ["get", "colour"],
      "circle-opacity": ["case", ["==", ["get", "kind"], "incident"], 0.18, 1],
      "circle-radius": ["case", ["==", ["get", "kind"], "incident"], MARKER.incident + 2, MARKER.point + 1],
      "circle-stroke-color": ["case", ["==", ["get", "kind"], "incident"], COLOURS.emergency, MARKER.outline],
      "circle-stroke-width": 2.5,
      "circle-pitch-alignment": "viewport",
    },
  })
  map.addLayer({
    id: "vehicles", type: "circle", source: "vehicles",
    filter: ["!", ["get", "emergency"]],
    paint: {
      "circle-color": ["get", "colour"],
      // Small when zoomed out, where thousands of vehicles share the screen
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 1.8, 14.5, 3, 17, MARKER.vehicle],
      "circle-stroke-color": MARKER.outline,
      "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 12, 0.5, 15, 1.5],
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

export default function Map3D({ data, onClick, onFailed }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const ready = useRef(false)
  // What each source last received, so unchanged sources are not re-sent
  const sent = useRef(new Map<SourceId, FeatureCollection>())
  // The newest props, for MapLibre's event handlers (set up once)
  const latest = useRef({ data, onClick, onFailed })
  useEffect(() => {
    latest.current = { data, onClick, onFailed }
  })

  function push() {
    const map = mapRef.current
    if (!map || !ready.current) return
    for (const id of SOURCES) {
      const collection = latest.current.data[id]
      if (sent.current.get(id) === collection) continue
      ;(map.getSource(id) as GeoJSONSource | undefined)?.setData(collection)
      sent.current.set(id, collection)
    }
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
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right")
    map.getCanvas().style.cursor = "crosshair"

    map.on("load", () => {
      addLayers(map)
      ready.current = true
      push()
      container.current?.setAttribute("data-ready", "true")
    })

    const popup = new maplibregl.Popup({ closeButton: false, offset: 14 })
    map.on("click", (event: MapLayerMouseEvent) => {
      const station = ready.current
        ? map.queryRenderedFeatures(event.point, { layers: STATION_LAYERS })[0]
        : undefined
      if (station) {
        // A station marker shows its numbers instead of placing a point
        const text = String(station.properties?.popup ?? "")
        const content = document.createElement("div")
        text.split("\n").forEach((line, index) => {
          const element = document.createElement(index === 0 ? "strong" : "div")
          element.textContent = line
          content.appendChild(element)
        })
        popup.setLngLat(event.lngLat).setDOMContent(content).addTo(map)
        return
      }
      latest.current.onClick([event.lngLat.lat, event.lngLat.lng])
    })
    for (const layer of STATION_LAYERS) {
      map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer" })
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = "crosshair" })
    }

    const sentSources = sent.current
    return () => {
      ready.current = false
      sentSources.clear()
      mapRef.current = null
      // Frees the WebGL context and its GPU memory
      map.remove()
    }
  }, [])

  useEffect(push, [data])

  return <div ref={container} className="map map-3d" data-testid="map-3d" />
}
