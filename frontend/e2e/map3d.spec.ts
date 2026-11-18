// The 3D map (MapLibre, OpenFreeMap tiles) driving the real simulation:
// clicks on it plan the route, and what it draws comes from the backend.
// Needs the internet for the map tiles, as the 2D map does.

import { expect, test, type Page } from "@playwright/test"

import { kpi, mapNotice } from "./helpers.ts"

const map3d = (page: Page) => page.locator('[data-testid="map-3d"]')

// How many features of the given layers the 3D map has drawn on screen
async function drawn(page: Page, layers: string[]): Promise<number> {
  return map3d(page).evaluate((element, layers) => {
    const map = (element as unknown as { maplibre: { queryRenderedFeatures(o: object): unknown[] } }).maplibre
    return map.queryRenderedFeatures({ layers }).length
  }, layers)
}

async function click3d(page: Page, x: number, y: number) {
  await map3d(page).scrollIntoViewIfNeeded()
  const box = await map3d(page).boundingBox()
  if (!box) throw new Error("The 3D map is not on the page")
  await page.mouse.click(box.x + box.width * x, box.y + box.height * y)
}

test("the 3D map plans a route and draws the live simulation", async ({ page }) => {
  await page.goto("/")
  await page.getByRole("button", { name: "3D" }).click()
  await expect(map3d(page)).toHaveAttribute("data-ready", "true", { timeout: 30_000 })
  await expect(page.locator(".leaflet-container")).toHaveCount(0)

  // Norwich's buildings are drawn in 3D
  await expect.poll(() => drawn(page, ["buildings-3d"]), { timeout: 20_000 }).toBeGreaterThan(100)

  await click3d(page, 0.3, 0.65)
  await click3d(page, 0.7, 0.35)
  await page.getByRole("button", { name: "Calculate Route" }).click()
  await expect(kpi(page, "Status")).toContainText("Running", { timeout: 30_000 })

  // The route and the backend's vehicles are on the map
  await expect.poll(() => drawn(page, ["routes"])).toBeGreaterThan(0)
  const live = page.getByRole("region", { name: "Live controls" })
  await live.getByLabel("Fleet size").selectOption("500")
  await live.getByRole("button", { name: "Add vehicles" }).click()
  await expect(mapNotice(page)).toContainText(/Added 500 vehicles/)
  await expect.poll(() => drawn(page, ["vehicles"]), { timeout: 10_000 }).toBeGreaterThan(50)
  await expect.poll(() => drawn(page, ["congestion"]), { timeout: 10_000 }).toBeGreaterThan(0)

  // An emergency: its vehicle gets its own marker and route
  await page.getByRole("button", { name: "Trigger Ambulance Emergency" }).click()
  await expect(mapNotice(page)).toContainText(/dispatched/i, { timeout: 15_000 })
  await expect.poll(() => drawn(page, ["emergency-vehicles"]), { timeout: 10_000 }).toBeGreaterThan(0)
  await expect.poll(() => drawn(page, ["routes-emergency"])).toBeGreaterThan(0)

  // Back to 2D keeps the same run
  await page.getByRole("button", { name: "2D" }).click()
  await expect(page.locator(".leaflet-container")).toBeVisible()
  await expect(kpi(page, "Status")).toContainText("Running")
})
