// The command centre's 2D map, for browsers where the 3D map cannot start
// (no WebGL): the same features (command.ts) drawn with Leaflet, as the
// rest of the site's 2D map is.

import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip, useMapEvents } from "react-leaflet"

import type { FeatureCollection, SourceId } from "./mapData.ts"
import { COLOURS, MARKER } from "./mapStyles.ts"
import type { Coordinate } from "./simulationHelpers.ts"

type LatLng = [number, number]

const latLng = ([lon, lat]: number[]): LatLng => [lat, lon]

function Clicks({ onClick }: { onClick: (location: Coordinate) => void }) {
  useMapEvents({ click: (event) => onClick([event.latlng.lat, event.latlng.lng]) })
  return null
}

export default function CommandMap2D({
  data,
  onClick,
}: {
  data: Record<SourceId, FeatureCollection>
  onClick: (location: Coordinate) => void
}) {
  const lines = (id: SourceId) =>
    data[id].features.filter((f) => f.geometry.type === "LineString")
  const points = (id: SourceId) =>
    data[id].features.filter((f) => f.geometry.type === "Point")

  return (
    <MapContainer className="map command-map" center={[52.6309, 1.2974]} zoom={13}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <Clicks onClick={onClick} />
      {lines("congestion").map((f, i) => (
        <Polyline key={`c${i}`} positions={(f.geometry.coordinates as number[][]).map(latLng)}
          pathOptions={{ color: String(f.properties.colour), weight: 4, opacity: 0.7 }} />
      ))}
      {[...lines("closures"), ...lines("floods")].map((f, i) => (
        <Polyline key={`x${i}`} positions={(f.geometry.coordinates as number[][]).map(latLng)}
          pathOptions={{ color: COLOURS.closure, weight: 7, opacity: 0.9 }} />
      ))}
      {lines("routes").map((f) => (
        <Polyline key={`r${f.properties.unit}`} positions={(f.geometry.coordinates as number[][]).map(latLng)}
          pathOptions={{ color: String(f.properties.colour), weight: 4, dashArray: "8, 7" }} />
      ))}
      {points("stations").map((f, i) => (
        <CircleMarker key={`s${i}`} center={latLng(f.geometry.coordinates as number[])} radius={MARKER.chosenStation}
          pathOptions={{ color: "#ffffff", fillColor: String(f.properties.colour), fillOpacity: 1, weight: 2 }}>
          <Tooltip>{String(f.properties.popup).split("\n")[0]}</Tooltip>
        </CircleMarker>
      ))}
      {points("points").map((f) => (
        <CircleMarker key={`i${f.properties.incident}`} center={latLng(f.geometry.coordinates as number[])} radius={MARKER.incident + 4}
          pathOptions={{ color: String(f.properties.colour), fillColor: String(f.properties.colour), fillOpacity: 0.3, weight: 3 }}>
          <Tooltip permanent>{String(f.properties.label)}</Tooltip>
        </CircleMarker>
      ))}
      {points("vehicles").filter((f) => f.properties.emergency).map((f) => (
        <CircleMarker key={`v${f.properties.id}`} center={latLng(f.geometry.coordinates as number[])} radius={MARKER.vehicle + 2}
          pathOptions={{ color: "#ffffff", fillColor: String(f.properties.colour), fillOpacity: 1, weight: 2 }}>
          <Tooltip>{`${f.properties.letter} ${f.properties.id}`}</Tooltip>
        </CircleMarker>
      ))}
    </MapContainer>
  )
}
