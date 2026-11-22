// The 3D digital twin's environment: where the sun is over Norwich (for
// the lighting and sky), what the live weather does to the view, saved and
// preset viewpoints, the population and coverage layers (from the
// backend's own hexagons, GET /optimiser/plan) and congestion extruded in
// 3D. No React or MapLibre here: tests/twin.test.ts.
//
// Everything stays grounded: the sun is computed for Norwich's latitude
// and longitude, the weather is Open-Meteo's current reading for Norwich
// (GET /city/weather), and the layers are the model's own figures.

import type { Feature, FeatureCollection } from "./mapData.ts"
import { coverageFeatures, existingStage, type Plan } from "./planning.ts"

export const NORWICH = { latitude: 52.6286, longitude: 1.2925 }

const RAD = Math.PI / 180

// ------------------------------------------------
// The sun
// ------------------------------------------------

// The sun's altitude (degrees above the horizon) and azimuth (degrees
// clockwise from north) at a moment, for a place: the NOAA solar position
// equations (accurate to a fraction of a degree, plenty for lighting)
export function sunPosition(date: Date, latitude = NORWICH.latitude, longitude = NORWICH.longitude) {
  const day = date.getTime() / 86_400_000 + 2_440_587.5 - 2_451_545 // days since J2000
  const meanLongitude = (280.46 + 0.9856474 * day) % 360
  const meanAnomaly = ((357.528 + 0.9856003 * day) % 360) * RAD
  const eclipticLongitude =
    (meanLongitude + 1.915 * Math.sin(meanAnomaly) + 0.02 * Math.sin(2 * meanAnomaly)) * RAD
  const obliquity = (23.439 - 0.0000004 * day) * RAD
  const declination = Math.asin(Math.sin(obliquity) * Math.sin(eclipticLongitude))
  const rightAscension = Math.atan2(Math.cos(obliquity) * Math.sin(eclipticLongitude), Math.cos(eclipticLongitude))
  const siderealTime = (280.16 + 360.9856235 * day + longitude) * RAD
  const hourAngle = siderealTime - rightAscension
  const lat = latitude * RAD
  const altitude = Math.asin(
    Math.sin(lat) * Math.sin(declination) + Math.cos(lat) * Math.cos(declination) * Math.cos(hourAngle),
  )
  const azimuth = Math.atan2(
    Math.sin(hourAngle),
    Math.cos(hourAngle) * Math.sin(lat) - Math.tan(declination) * Math.cos(lat),
  )
  return { altitude: altitude / RAD, azimuth: ((azimuth / RAD + 180) % 360 + 360) % 360 }
}

export type Phase = "day" | "golden" | "twilight" | "night"

export function phaseOf(altitude: number): Phase {
  if (altitude >= 12) return "day"
  if (altitude >= 0) return "golden"
  if (altitude >= -8) return "twilight"
  return "night"
}

const mix = (a: number[], b: number[], t: number) => a.map((v, i) => Math.round(v + (b[i] - v) * t))
const rgb = (c: number[]) => `rgb(${c[0]}, ${c[1]}, ${c[2]})`
const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v))

export type Weather = {
  time: string
  temperature_c: number
  precipitation_mm: number
  rain_mm: number
  snowfall_cm: number
  cloud_cover_percent: number
  visibility_m: number | null
  wind_speed_kmh: number
  wind_direction_deg: number | null
  weather_code: number
  is_day: boolean
  source: string
}

// What the weather does to the view: rain or snow (0-1, from mm an hour),
// haze (0-1, from visibility and cloud), and a short description
export function weatherEffect(weather: Weather | null) {
  if (!weather) return { rain: 0, snow: 0, haze: 0, cloud: 0, label: "" }
  const rain = clamp(weather.rain_mm / 4)
  const snow = clamp(weather.snowfall_cm / 1.5)
  const visibility = weather.visibility_m ?? 30_000
  const haze = clamp(1 - visibility / 20_000) * 0.8 + clamp(weather.cloud_cover_percent / 100) * 0.2
  return {
    rain, snow, haze: clamp(haze), cloud: clamp(weather.cloud_cover_percent / 100),
    label: `${describeCode(weather.weather_code)}, ${Math.round(weather.temperature_c)} °C, wind ${Math.round(weather.wind_speed_kmh)} km/h`,
  }
}

// WMO weather codes, as Open-Meteo reports them
export function describeCode(code: number): string {
  if (code === 0) return "Clear"
  if (code <= 2) return "Partly cloudy"
  if (code === 3) return "Overcast"
  if (code === 45 || code === 48) return "Fog"
  if (code >= 51 && code <= 57) return "Drizzle"
  if (code >= 61 && code <= 67) return "Rain"
  if (code >= 71 && code <= 77) return "Snow"
  if (code >= 80 && code <= 82) return "Rain showers"
  if (code >= 85 && code <= 86) return "Snow showers"
  if (code >= 95) return "Thunderstorm"
  return "Unsettled"
}

// The light on the buildings (from the sun's direction) and the sky's
// colours, for the sun's altitude and the weather
export function atmosphere(sun: { altitude: number; azimuth: number }, weather: Weather | null) {
  const effect = weatherEffect(weather)
  const dayness = clamp((sun.altitude + 8) / 26) // 0 at night, 1 by mid-morning
  const golden = sun.altitude > -4 && sun.altitude < 10 ? 1 - Math.abs(sun.altitude - 3) / 7 : 0
  const grey = effect.cloud * 0.6 + effect.haze * 0.4
  const skyDay = mix([92, 146, 214], [150, 162, 176], grey)
  const skyNight = [9, 14, 26]
  const sky = mix(skyNight, skyDay, dayness)
  const horizonDay = mix([196, 214, 232], [176, 184, 192], grey)
  let horizon = mix([24, 34, 54], horizonDay, dayness)
  horizon = mix(horizon, [236, 160, 96], clamp(golden) * (1 - grey) * 0.75)
  const fog = mix([10, 14, 22], [168, 178, 190], dayness * (0.35 + grey * 0.65))
  // The light comes from where the sun is; at night, a dim cool fill from above
  const above = Math.max(sun.altitude, 0)
  return {
    phase: phaseOf(sun.altitude),
    light: {
      anchor: "map" as const,
      position: [1.5, sun.azimuth, clamp(90 - above, 20, 88)] as [number, number, number],
      color: sun.altitude > 0 ? rgb(mix([255, 250, 240], [255, 196, 140], clamp(golden))) : "rgb(150, 170, 210)",
      intensity: Number((0.22 + 0.33 * dayness * (1 - grey * 0.45)).toFixed(3)),
    },
    sky: {
      "sky-color": rgb(sky),
      "horizon-color": rgb(horizon),
      "fog-color": rgb(fog),
      "fog-ground-blend": Number((0.5 + 0.4 * effect.haze).toFixed(3)),
      "horizon-fog-blend": Number((0.6 + 0.35 * effect.haze).toFixed(3)),
      "sky-horizon-blend": 0.7,
      "atmosphere-blend": Number((0.6 + 0.3 * dayness).toFixed(3)),
    },
  }
}

// ------------------------------------------------
// Viewpoints
// ------------------------------------------------

export type Viewpoint = { name: string; center: [number, number]; zoom: number; pitch: number; bearing: number }

// Real places, looking the way that frames them best
export const VIEWPOINTS: Viewpoint[] = [
  { name: "City overview", center: [1.2935, 52.6295], zoom: 13.2, pitch: 50, bearing: -15 },
  { name: "Castle and Cathedral", center: [1.2985, 52.6305], zoom: 15.6, pitch: 62, bearing: -35 },
  { name: "Station and Riverside", center: [1.3075, 52.6265], zoom: 15.4, pitch: 60, bearing: 40 },
  { name: "Inner ring road", center: [1.2885, 52.6325], zoom: 14.6, pitch: 58, bearing: 120 },
  { name: "UEA and the hospital", center: [1.2305, 52.6205], zoom: 14.4, pitch: 55, bearing: -60 },
]

const SAVED_KEY = "norwich-sim-viewpoints"

export function savedViewpoints(): Viewpoint[] {
  try {
    const saved = JSON.parse(localStorage.getItem(SAVED_KEY) ?? "[]")
    return Array.isArray(saved) ? saved.filter((v) => v && Array.isArray(v.center) && typeof v.zoom === "number") : []
  } catch {
    return []
  }
}

export function saveViewpoints(viewpoints: Viewpoint[]) {
  try {
    localStorage.setItem(SAVED_KEY, JSON.stringify(viewpoints.slice(-8)))
  } catch {
    // Private browsing: kept for this visit only
  }
}

// ------------------------------------------------
// Analysis layers
// ------------------------------------------------

// A sequential ramp for densities: deep blue, teal, amber
const RAMP = [[29, 78, 137], [63, 181, 151], [242, 201, 76]]
export function rampColour(t: number): string {
  const x = clamp(t) * (RAMP.length - 1)
  const i = Math.min(RAMP.length - 2, Math.floor(x))
  return rgb(mix(RAMP[i], RAMP[i + 1], x - i))
}

// Residents per hexagon, extruded: the census weights the backend uses for
// every population figure (each hexagon's residents, from its junctions).
// Square-root scale, so the suburbs are not flattened by the densest cells.
export function populationFeatures(plan: Plan): FeatureCollection {
  const most = Math.max(1, ...plan.cells.map((c) => c.population))
  return {
    type: "FeatureCollection",
    features: plan.cells
      .map((cell, i) => ({ cell, i }))
      .filter(({ cell }) => cell.population >= 1)
      .map(({ cell, i }) => {
        const share = Math.sqrt(cell.population / most)
        return {
          type: "Feature",
          geometry: { type: "Polygon", coordinates: [cell.polygon] },
          properties: {
            cell: i,
            population: Math.round(cell.population),
            height: 6 + share * 320,
            colour: rampColour(share),
          },
        } satisfies Feature
      }),
  }
}

// Response time from the existing stations, per hexagon (the planning
// view's own colouring, its heights scaled down); null when the service has none mapped
export function existingCoverage(plan: Plan): FeatureCollection | null {
  const stage = existingStage(plan)
  if (!stage) return null
  const cells = coverageFeatures(plan, stage.cells.seconds, stage.cells.beyond_people)
  // A quarter of the planning view's heights: here the buildings and
  // traffic must stay visible at street level
  for (const feature of cells.features) feature.properties.height = Number(feature.properties.height) * 0.25
  return cells
}

// Live congestion as walls along each busy road: a thin strip either side
// of the road's line, as tall as its traffic
export function congestionWalls(congestion: FeatureCollection, metres = 4): FeatureCollection {
  const features: Feature[] = []
  for (const road of congestion.features) {
    if (road.geometry.type !== "LineString") continue
    const vehicles = Number(road.properties.vehicles ?? 0)
    if (vehicles < 2) continue
    const points = road.geometry.coordinates
    for (let i = 0; i < points.length - 1; i++) {
      const [lon1, lat1] = points[i]
      const [lon2, lat2] = points[i + 1]
      const kx = 111_320 * Math.cos(lat1 * RAD)
      const dx = (lon2 - lon1) * kx
      const dy = (lat2 - lat1) * 111_320
      const length = Math.hypot(dx, dy)
      if (length < 0.5) continue
      // Perpendicular, `metres` either side
      const ox = (-dy / length) * metres / kx
      const oy = (dx / length) * metres / 111_320
      features.push({
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [[[lon1 + ox, lat1 + oy], [lon2 + ox, lat2 + oy], [lon2 - ox, lat2 - oy], [lon1 - ox, lat1 - oy], [lon1 + ox, lat1 + oy]]],
        },
        properties: { vehicles, colour: String(road.properties.colour ?? "#e0a14a"), height: Math.min(220, 6 + vehicles * 6) },
      })
    }
  }
  return { type: "FeatureCollection", features }
}

// ------------------------------------------------
// Performance
// ------------------------------------------------

// Whether to start in performance mode: graphics drawn in software (no
// GPU), few cores, little memory, or a big simulation. Terrain and the
// finer effects are then off.
export function preferLite(
  device: { cores?: number; memoryGb?: number; reducedMotion?: boolean; software?: boolean },
  vehicles = 0,
): boolean {
  return Boolean(device.software) ||
    (device.cores !== undefined && device.cores <= 4) ||
    (device.memoryGb !== undefined && device.memoryGb <= 4) ||
    Boolean(device.reducedMotion) || vehicles > 3000
}

// Whether WebGL is drawn in software (SwiftShader, llvmpipe and the like):
// every frame then costs the CPU, so the heavier effects stay off
export function softwareRenderer(renderer: string | null | undefined): boolean {
  return /swiftshader|llvmpipe|software|softpipe|basic render/i.test(renderer ?? "")
}
