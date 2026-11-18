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
  // Scenario analysis: the roads that lost the most time (amber, the
  // palette's "waiting" colour), drawn wide and see-through
  delayHotspot: "#a86d17",
  // Flooded roads: water, kept apart from the route's darker blue
  flood: "#1aa0c8",
} as const

// Traffic signals: what each approach's driver sees
export const SIGNAL_LIGHTS = {
  green: "#1e8e4f",
  amber: "#d99a06",
  red: "#c0392b",
} as const

// Point markers share one language: a solid fill with a white keyline,
// sized by importance.
export const MARKER = {
  outline: "#ffffff",
  outlineWeight: 2,
  point: 7,
  // Ordinary vehicles: small, so they don't drown congestion and incidents
  vehicle: 3,
  ambulance: 8,
  incident: 13,
  candidate: 11,
  chosenStation: 12,
} as const

// Line weights, from the widest (drawn underneath) to the narrowest
export const LINE = {
  delayHotspot: 10,
  closure: 8,
  congestion: 9,
  emergency: 4,
  reroute: 4,
  route: 3.5,
} as const

// 3D buildings: taller ones a little darker, in the app's slate blues.
// Heights in metres where each colour applies (blended in between).
export const BUILDINGS = [
  { height: 0, colour: "#253242", label: "Under 15 m" },
  { height: 15, colour: "#34465b", label: "15-40 m" },
  { height: 40, colour: "#4a6380", label: "Over 40 m" },
] as const

// Ordinary vehicles on the 3D map: understated, so congestion, incidents
// and emergency vehicles stand out
export const VEHICLE_3D = "#b9c6d4"
