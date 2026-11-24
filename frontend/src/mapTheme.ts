// The 3D map's own style: a dark, restrained "operations" basemap built
// from OpenFreeMap's OpenMapTiles vector data (OpenStreetMap), so the
// simulation layers on top carry the colour.
//
// Everything drawn is real map data from the tiles:
//   water, waterway   the Wensum and Yare and other water
//   park, landcover   parks, woods and grass, kept dim
//   transportation    roads by class (motorway > trunk > primary >
//                     secondary > tertiary > minor > service), rail, bridges
//   building          extruded by Map3D.tsx from render_height
//   *_name, place     a few labels: river, main roads, places
// There is no terrain: OpenFreeMap has no elevation tiles, and Norwich is
// low and flat (the map would gain little). No API key, as before.

import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from "maplibre-gl"

import { BUILDINGS } from "./mapStyles.ts"

export const OPS = {
  background: "#0e151d",
  land: "#121b25",
  residential: "#141f2a",
  green: "#142420",
  water: "#12304a",
  waterway: "#1d4a6e",
  rail: "#3a4757",
  roadMinor: "#26323f",
  roadMid: "#334255",
  roadMajor: "#46596f",
  roadTrunk: "#5a7088",
  casing: "#0b1118",
  label: "#8fa1b5",
  labelHalo: "#0e151d",
  waterLabel: "#5d86ab",
} as const

const ROAD_CLASSES = {
  major: ["motorway", "trunk", "primary"],
  mid: ["secondary", "tertiary"],
  minor: ["minor", "service"],
}

// Road width in pixels by zoom, scaled for its class
function width(scale: number): ExpressionSpecification {
  return ["interpolate", ["exponential", 1.6], ["zoom"], 11, 0.4 * scale, 14, 1.6 * scale, 17, 7 * scale, 19, 20 * scale]
}

const isClass = (classes: string[]): ExpressionSpecification =>
  ["match", ["get", "class"], classes, true, false]

export const OPS_STYLE: StyleSpecification = {
  version: 8,
  name: "Norwich Urban Traffic Simulator operations",
  glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
  sources: {
    openmaptiles: { type: "vector", url: "https://tiles.openfreemap.org/planet" },
  },
  // Light for the extruded buildings and vehicles: low, from the north-west,
  // so walls facing the viewer read as darker
  light: { anchor: "viewport", color: "#ffffff", intensity: 0.35, position: [1.4, 210, 35] },
  layers: [
    { id: "background", type: "background", paint: { "background-color": OPS.background } },
    {
      id: "landuse-residential", type: "fill", source: "openmaptiles", "source-layer": "landuse",
      filter: ["match", ["get", "class"], ["residential", "suburb", "neighbourhood"], true, false],
      paint: { "fill-color": OPS.residential },
    },
    {
      id: "landcover-green", type: "fill", source: "openmaptiles", "source-layer": "landcover",
      filter: ["match", ["get", "class"], ["wood", "grass", "farmland", "wetland"], true, false],
      paint: { "fill-color": OPS.green, "fill-opacity": 0.8 },
    },
    {
      id: "park", type: "fill", source: "openmaptiles", "source-layer": "park",
      paint: { "fill-color": OPS.green },
    },
    {
      id: "water", type: "fill", source: "openmaptiles", "source-layer": "water",
      filter: ["!=", ["get", "brunnel"], "tunnel"],
      paint: { "fill-color": OPS.water },
    },
    {
      id: "waterway", type: "line", source: "openmaptiles", "source-layer": "waterway",
      paint: {
        "line-color": OPS.waterway,
        "line-width": ["interpolate", ["linear"], ["zoom"], 11, 0.8, 16, 3],
      },
    },
    {
      id: "rail", type: "line", source: "openmaptiles", "source-layer": "transportation",
      filter: ["match", ["get", "class"], ["rail", "transit"], true, false],
      paint: { "line-color": OPS.rail, "line-width": 1.2, "line-dasharray": [3, 2] },
    },
    // Roads: a dark casing under every road, then the road by class
    {
      id: "road-casing", type: "line", source: "openmaptiles", "source-layer": "transportation",
      filter: isClass([...ROAD_CLASSES.major, ...ROAD_CLASSES.mid]),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": OPS.casing, "line-width": width(1.5), "line-gap-width": 0 },
    },
    {
      id: "road-minor", type: "line", source: "openmaptiles", "source-layer": "transportation",
      filter: isClass(ROAD_CLASSES.minor),
      minzoom: 12.5,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": OPS.roadMinor, "line-width": width(0.55) },
    },
    {
      id: "road-mid", type: "line", source: "openmaptiles", "source-layer": "transportation",
      filter: isClass(ROAD_CLASSES.mid),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": OPS.roadMid, "line-width": width(0.85) },
    },
    {
      id: "road-major", type: "line", source: "openmaptiles", "source-layer": "transportation",
      filter: isClass(ROAD_CLASSES.major),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": ["match", ["get", "class"], ["motorway", "trunk"], OPS.roadTrunk, OPS.roadMajor],
        "line-width": width(1.15),
      },
    },
    // Labels: few, quiet
    {
      id: "water-name", type: "symbol", source: "openmaptiles", "source-layer": "waterway",
      minzoom: 13,
      layout: {
        "symbol-placement": "line", "text-field": ["get", "name"],
        "text-font": ["Noto Sans Italic"], "text-size": 11,
      },
      paint: { "text-color": OPS.waterLabel, "text-halo-color": OPS.labelHalo, "text-halo-width": 1 },
    },
    {
      id: "road-name", type: "symbol", source: "openmaptiles", "source-layer": "transportation_name",
      minzoom: 14.5,
      filter: isClass([...ROAD_CLASSES.major, ...ROAD_CLASSES.mid]),
      layout: {
        "symbol-placement": "line", "text-field": ["get", "name"],
        "text-font": ["Noto Sans Regular"], "text-size": 10.5,
      },
      paint: { "text-color": OPS.label, "text-halo-color": OPS.labelHalo, "text-halo-width": 1.2 },
    },
    {
      id: "place-name", type: "symbol", source: "openmaptiles", "source-layer": "place",
      filter: ["match", ["get", "class"], ["city", "town", "suburb", "quarter", "neighbourhood"], true, false],
      layout: {
        "text-field": ["get", "name"], "text-font": ["Noto Sans Bold"],
        "text-size": ["match", ["get", "class"], "city", 15, 11],
        "text-transform": "uppercase", "text-letter-spacing": 0.08,
      },
      paint: { "text-color": OPS.label, "text-halo-color": OPS.labelHalo, "text-halo-width": 1.2 },
    },
  ],
}

// A few buildings come with no height at all; treat them as flat
const BUILDING_HEIGHT: ExpressionSpecification = ["coalesce", ["get", "render_height"], 0]

// Buildings, extruded to their mapped height (OpenMapTiles' render_height:
// the OSM height, or floors x 3.66 m, or a default when neither is mapped),
// shared by the 3D map and the planning view
export function buildingsLayer(opacity = 0.92): LayerSpecification {
  return {
    id: "buildings-3d", type: "fill-extrusion", source: "openmaptiles", "source-layer": "building",
    minzoom: 13,
    filter: ["!", ["has", "hide_3d"]],
    paint: {
      "fill-extrusion-color": [
        "interpolate", ["linear"], BUILDING_HEIGHT,
        ...BUILDINGS.flatMap((step) => [step.height, step.colour]),
      ] as ExpressionSpecification,
      "fill-extrusion-height": ["interpolate", ["linear"], ["zoom"], 13, 0, 14.5, BUILDING_HEIGHT],
      "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
      "fill-extrusion-opacity": opacity,
      "fill-extrusion-vertical-gradient": true,
    },
  }
}
