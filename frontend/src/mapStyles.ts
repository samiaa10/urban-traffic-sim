// Colours used on the map, in one place so the map, the legend and the
// tests all agree. They match the palette in index.css.

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
  // The new route after a road closure
  reroute: "#d9822b",
  // Station optimiser: best candidate, other candidates, chosen set
  rankBest: "#2f7d6b",
  rank: "#4a6fa5",
  chosen: "#173e60",
} as const
