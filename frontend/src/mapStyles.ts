// Colours and symbol sizes used on the map, in one place so the map, the
// legend and the tests all agree. They match the palette in index.css.
//
// Colour carries meaning only: blue = planned journey, red = emergency,
// violet = diversion, near-black = closed road, teal/slate/navy = stations.
// Congestion keeps its own green / amber / red scale (simulationHelpers.ts).

export const COLOURS = {
  // The route the vehicle follows
  route: "#2a5d8f",
  // Start and destination points
  start: "#2f7d6b",
  destination: "#173e60",
  // Ambulance response route, the ambulance, and the emergency itself
  emergency: "#b4423a",
  // A road closed ahead of the ambulance
  closure: "#1b2733",
  // The new route after a road closure. Violet, so it cannot be mistaken
  // for the amber "moderate" congestion band underneath it.
  reroute: "#6a55a3",
  // Station optimiser: best candidate, other candidates, chosen set
  rankBest: "#2f7d6b",
  rank: "#5b7699",
  chosen: "#173e60",
} as const

// Point markers share one language: a solid fill with a white keyline,
// sized by importance.
export const MARKER = {
  outline: "#ffffff",
  outlineWeight: 2,
  point: 7,
  vehicle: 6,
  ambulance: 8,
  incident: 13,
  candidate: 11,
  chosenStation: 12,
} as const

// Line weights, from the widest (drawn underneath) to the narrowest
export const LINE = {
  closure: 8,
  congestion: 9,
  emergency: 4,
  reroute: 4,
  route: 3.5,
} as const
