// Shared between the stand-in map (fakeLeaflet.tsx) and the test helpers
// (helpers.ts): the click handler the app registered with useMapEvents,
// so a test can "click the map".

export type MapClick = { latlng: { lat: number; lng: number } }

type Handlers = { click?: (event: MapClick) => void }

let handlers: Handlers = {}

export function setMapHandlers(next: Handlers) {
  handlers = next
}

export function getMapHandlers(): Handlers {
  return handlers
}

export function resetMap() {
  handlers = {}
}
