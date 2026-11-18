// Shared steps for the end-to-end tests.

import { expect, type Page } from "@playwright/test"

export const API_URL = "http://127.0.0.1:8002"

// Click the map at a point given as fractions of its width and height
export async function clickMap(page: Page, x: number, y: number) {
  const map = page.locator(".leaflet-container")
  // Positions are on screen: the map must be in view to be clicked
  await map.scrollIntoViewIfNeeded()
  const box = await map.boundingBox()
  if (!box) throw new Error("The map is not on the page")
  await page.mouse.click(box.x + box.width * x, box.y + box.height * y)
}

// Click a geographic point on the 2D map, whatever size the map is drawn
// at: from its starting view (centre 52.6309, 1.2974, zoom 13, as App.tsx
// sets it), in Web Mercator pixels
export async function clickMapAt(page: Page, latitude: number, longitude: number) {
  const map = page.locator(".leaflet-container")
  await map.scrollIntoViewIfNeeded()
  const box = await map.boundingBox()
  if (!box) throw new Error("The map is not on the page")
  const size = 256 * 2 ** 13
  const x = (lon: number) => ((lon + 180) / 360) * size
  const y = (lat: number) => {
    const s = Math.sin((lat * Math.PI) / 180)
    return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * size
  }
  await page.mouse.click(
    box.x + box.width / 2 + x(longitude) - x(1.2974),
    box.y + box.height / 2 + y(latitude) - y(52.6309),
  )
}

// The status notice floating over the map (what the last action did)
export const mapNotice = (page: Page) => page.locator(".map-notice")

export const kpi = (page: Page, label: string) =>
  page.getByLabel("Simulation metrics").locator(".kpi", { hasText: label })

// Pick a start and a destination across central Norwich and calculate the
// route; resolves once the simulation is running
export async function planRoute(page: Page, from: [number, number] = [0.35, 0.45], to: [number, number] = [0.65, 0.55]) {
  await page.goto("/")
  await expect(page.getByRole("heading", { name: "Norwich road network" })).toBeVisible()
  await clickMap(page, ...from)
  await clickMap(page, ...to)
  await page.getByRole("button", { name: "Calculate Route" }).click()
  await expect(kpi(page, "Route length")).toContainText("m", { timeout: 30_000 })
  await expect(kpi(page, "Route length")).not.toContainText("No route calculated")
}

// The simulated time shown on the page, in seconds
export async function simulationTime(page: Page): Promise<number> {
  const text = await kpi(page, "Simulation time").locator(".kpi-value").innerText()
  return Number(text.replace(/[^0-9]/g, ""))
}
