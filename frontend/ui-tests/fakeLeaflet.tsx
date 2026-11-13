// A stand-in for react-leaflet. Real Leaflet needs a real browser layout,
// which jsdom does not have, so each map object is drawn as a plain element
// whose data-* attributes say what the app asked Leaflet to draw:
//
//   circle markers  data-testid="circle-marker", data-center="lat,lng",
//                   data-radius, data-color (outline), data-fill
//   polylines       data-testid="polyline", data-points (how many points),
//                   data-color
//   tooltips        data-testid="tooltip" (the numbers on station markers)
//
// The tests therefore check what the app decides to draw, not Leaflet.
// Used with: vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

/* eslint-disable react-refresh/only-export-components */

import type { ReactNode } from "react"

import { setMapHandlers, type MapClick } from "./mapState.ts"

type PathOptions = { color?: string; fillColor?: string }

type LatLng = [number, number]

export function MapContainer({ children }: { children?: ReactNode }) {
  return <div data-testid="map">{children}</div>
}

export function TileLayer() {
  return null
}

export function CircleMarker({
  center,
  radius,
  pathOptions,
  children,
}: {
  center: LatLng
  radius?: number
  pathOptions?: PathOptions
  children?: ReactNode
}) {
  return (
    <div
      data-testid="circle-marker"
      data-center={center.join(",")}
      data-radius={radius}
      data-color={pathOptions?.color}
      data-fill={pathOptions?.fillColor}
    >
      {children}
    </div>
  )
}

export function Polyline({
  positions,
  pathOptions,
}: {
  positions: LatLng[]
  pathOptions?: PathOptions
}) {
  // Leaflet draws nothing for a line without points
  if (positions.length === 0) {
    return null
  }

  return (
    <div
      data-testid="polyline"
      data-points={positions.length}
      data-color={pathOptions?.color}
    />
  )
}

export function Tooltip({ children }: { children?: ReactNode }) {
  return <span data-testid="tooltip">{children}</span>
}

// Popups only appear after a click on a marker in the real map
export function Popup() {
  return null
}

export function useMapEvents(handlers: { click?: (event: MapClick) => void }) {
  // Re-registered on every render, so a click always reaches the app's
  // latest handler (it closes over the current state)
  setMapHandlers(handlers)
  return null
}
