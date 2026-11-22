// The city model's figures (GET /city/overview): the road network, the
// census population, the mapped stations, live runs and the AI analyst's
// provider. Read once when the page opens.

import { useEffect, useState } from "react"

import { API_URL } from "./config.ts"

export type Overview = {
  junctions: number
  road_segments: number
  road_km: number
  signalised_junctions: number | null
  osm_stations: { ambulance: number; fire: number; police: number } | null
  population: { residents: number; output_areas: number; source: string } | null
  live_runs: number
  database: string
  ai: { provider: string; model: string | null; available: boolean; reason: string | null }
}

export function useOverview(): Overview | null {
  const [overview, setOverview] = useState<Overview | null>(null)
  useEffect(() => {
    let current = true
    fetch(`${API_URL}/city/overview`)
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => { if (current && body) setOverview(body) })
      .catch(() => {})
    return () => { current = false }
  }, [])
  return overview
}

