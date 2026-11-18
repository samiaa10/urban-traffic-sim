// Vehicles as 3D bodies on the 3D map, from the simulation's own positions.
//
// The backend sends each vehicle's position along the real shape of its
// road and the way it faces (routing/road_geometry.py). Here each becomes a
// footprint polygon (its real length and width, rotated to its heading and
// set just left of the road's centre line, as UK traffic drives), which the
// map extrudes into a box in one batched layer.
//
// Between the backend's updates (one a second) a vehicle glides from its
// last reported position to its new one (interpolate): both ends are real
// simulation positions; nothing is invented in between but the straight
// glide. Vehicles that appear or vanish are not animated.

import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl"

import type { Feature, FeatureCollection } from "./mapData.ts"

type Pose = { lat: number; lon: number; heading: number }

// Metres: length, width and height of each kind of vehicle
export const BODY = {
  car: { length: 4.5, width: 1.9, height: 1.5 },
  emergency: { length: 6.5, width: 2.3, height: 2.6 },
} as const

// How far left of the road's centre line vehicles drive (metres)
export const LANE_OFFSET = 1.8

const METRES_PER_DEGREE = 111_320

function offset(lat: number, lon: number, north: number, east: number): [number, number] {
  return [
    lon + east / (METRES_PER_DEGREE * Math.cos((lat * Math.PI) / 180)),
    lat + north / METRES_PER_DEGREE,
  ]
}

// Size multiplier by zoom: true size from zoom 18, up to 3x when zoomed out
// so a car stays visible
export function bodyScale(zoom: number): number {
  return Math.min(3, Math.max(1, 2 ** (18 - zoom)))
}

// The footprint of one vehicle (a rectangle, its front facing `heading`)
export function footprint(pose: Pose, emergency: boolean, scale = 1): [number, number][] {
  const size = emergency ? BODY.emergency : BODY.car
  const radians = (pose.heading * Math.PI) / 180
  // Unit vectors: forward (along the heading) and to the left of it
  const fn = Math.cos(radians), fe = Math.sin(radians)
  const ln = fe, le = -fn
  const half = (size.length * scale) / 2, side = (size.width * scale) / 2
  const shift = LANE_OFFSET
  const corner = (along: number, across: number) =>
    offset(pose.lat, pose.lon, fn * along + ln * (across + shift), fe * along + le * (across + shift))
  const ring = [corner(half, side), corner(half, -side), corner(-half, -side), corner(-half, side)]
  return [...ring, ring[0]]
}

export function bodyFeatures(vehicles: Feature[], poses: Map<string | number, Pose>, scale: number): FeatureCollection {
  const features: Feature[] = []
  for (const vehicle of vehicles) {
    const pose = poses.get(vehicle.properties.id as number)
    if (!pose) continue
    const emergency = Boolean(vehicle.properties.emergency)
    features.push({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [footprint(pose, emergency, scale)] },
      properties: {
        ...vehicle.properties,
        height: (emergency ? BODY.emergency : BODY.car).height * Math.min(scale, 1.6),
      },
    })
  }
  return { type: "FeatureCollection", features }
}

// The pose a fraction t of the way from `from` to `to` (turning the short way)
export function interpolate(from: Pose, to: Pose, t: number): Pose {
  const turn = ((to.heading - from.heading + 540) % 360) - 180
  return {
    lat: from.lat + (to.lat - from.lat) * t,
    lon: from.lon + (to.lon - from.lon) * t,
    heading: (from.heading + turn * t + 360) % 360,
  }
}

export function poseOf(vehicle: Feature): Pose {
  const [lon, lat] = vehicle.geometry.coordinates as [number, number]
  return { lat, lon, heading: Number(vehicle.properties.heading ?? 0) }
}

// Above this many vehicles they jump to each new position instead of
// gliding (each glide frame re-sends every vehicle)
export const GLIDE_LIMIT = 3000
// Shortest gap between glide frames (ms): about 20 a second
const FRAME_MS = 50

// Draws the vehicles on a map, gliding them between updates
export class VehicleGlide {
  from = new Map<string | number, Pose>()
  to = new Map<string | number, Pose>()
  features: Feature[] = []
  start = 0
  duration = 1
  lastTarget = 0
  lastDraw = 0
  frame = 0

  posesAt(time: number) {
    const t = Math.min(1, (time - this.start) / this.duration)
    const poses = new Map<string | number, Pose>()
    for (const [id, to] of this.to) {
      const from = this.from.get(id)
      poses.set(id, from ? interpolate(from, to, t) : to)
    }
    return { poses, t }
  }

  draw(map: MapLibreMap, time: number): number {
    const { poses, t } = this.posesAt(time)
    const points: FeatureCollection = {
      type: "FeatureCollection",
      features: this.features.map((feature) => {
        const pose = poses.get(feature.properties.id as number)
        return pose ? { ...feature, geometry: { type: "Point" as const, coordinates: [pose.lon, pose.lat] } } : feature
      }),
    }
    ;(map.getSource("vehicles") as GeoJSONSource | undefined)?.setData(points)
    if (map.getZoom() >= 15) {
      ;(map.getSource("vehicle-bodies") as GeoJSONSource | undefined)?.setData(
        bodyFeatures(this.features, poses, bodyScale(map.getZoom())),
      )
    }
    this.lastDraw = time
    return t
  }

  // New positions from the backend: glide to them over about the time
  // since the last ones came
  set(map: MapLibreMap, collection: FeatureCollection) {
    const now = performance.now()
    this.from = this.features.length ? this.posesAt(now).poses : new Map()
    this.to = new Map(collection.features.map((feature) => [feature.properties.id as number, poseOf(feature)]))
    this.features = collection.features
    this.duration = this.lastTarget ? Math.min(1500, Math.max(250, now - this.lastTarget)) : 1
    this.lastTarget = now
    this.start = now
    if (collection.features.length > GLIDE_LIMIT) {
      this.from = new Map()
      this.draw(map, now)
      return
    }
    const tick = (time: number) => {
      this.frame = 0
      const t = time - this.lastDraw >= FRAME_MS
        ? this.draw(map, time)
        : Math.min(1, (time - this.start) / this.duration)
      if (t < 1) this.frame = requestAnimationFrame(tick)
      else if (this.lastDraw < this.start + this.duration) this.draw(map, time)
    }
    if (!this.frame) this.frame = requestAnimationFrame(tick)
  }

  stop() {
    if (this.frame) cancelAnimationFrame(this.frame)
    this.frame = 0
  }
}
