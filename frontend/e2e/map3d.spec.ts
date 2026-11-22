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

// Where the camera is looking
async function centre(page: Page): Promise<string> {
  return map3d(page).evaluate((element) => {
    const map = (element as unknown as { maplibre: { getCenter(): { lng: number; lat: number } } }).maplibre
    const { lng, lat } = map.getCenter()
    return `${lng.toFixed(4)},${lat.toFixed(4)}`
  })
}

// The middle of a live traffic segment, on screen, with no vehicle or
// message on top of it
async function trafficSpot(page: Page): Promise<{ x: number; y: number } | null> {
  return map3d(page).evaluate((element) => {
    type Map = {
      queryRenderedFeatures(geometry: unknown, options?: object): { geometry: { coordinates: number[][] } }[]
      project(position: number[]): { x: number; y: number }
    }
    const map = (element as unknown as { maplibre: Map }).maplibre
    const box = element.getBoundingClientRect()
    for (const feature of map.queryRenderedFeatures({ layers: ["congestion"] })) {
      const [a, b] = feature.geometry.coordinates
      const point = map.project([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])
      const x = box.x + point.x, y = box.y + point.y
      const clear =
        point.x > 60 && point.x < box.width - 60 && point.y > 120 && point.y < box.height * 0.6 &&
        element.ownerDocument.elementFromPoint(x, y)?.tagName === "CANVAS" &&
        map.queryRenderedFeatures(
          [[point.x - 10, point.y - 10], [point.x + 10, point.y + 10]],
          { layers: ["vehicles", "emergency-vehicles"] },
        ).length === 0
      if (clear) return { x, y }
    }
    return null
  })
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

  const startingView = await centre(page)
  await click3d(page, 0.3, 0.65)
  await click3d(page, 0.7, 0.35)
  await page.getByRole("button", { name: "Calculate Route" }).click()
  await expect(kpi(page, "Status")).toContainText("Running", { timeout: 30_000 })

  // The route and the backend's vehicles are on the map
  await expect.poll(() => drawn(page, ["routes"])).toBeGreaterThan(0)
  // ...and the camera moved to frame it
  await expect.poll(() => centre(page)).not.toBe(startingView)
  const live = page.getByRole("region", { name: "Live controls" })
  await live.getByLabel("Fleet size").selectOption("500")
  await live.getByRole("button", { name: "Add vehicles" }).click()
  await expect(mapNotice(page)).toContainText(/Added 500 vehicles/)
  await expect.poll(() => drawn(page, ["vehicles"]), { timeout: 10_000 }).toBeGreaterThan(50)
  await expect.poll(() => drawn(page, ["congestion"]), { timeout: 10_000 }).toBeGreaterThan(0)

  // Layers: the buildings can be switched off and on
  await page.getByRole("button", { name: "Layers" }).click()
  await page.getByLabel("3D buildings").uncheck()
  await expect.poll(() => drawn(page, ["buildings-3d"])).toBe(0)
  await page.getByLabel("3D buildings").check()
  await expect.poll(() => drawn(page, ["buildings-3d"])).toBeGreaterThan(100)
  await page.keyboard.press("Escape")

  // Inspect: clicking a road with traffic describes it instead of placing
  // a point, with the backend's live numbers
  await page.getByRole("button", { name: "Inspect" }).click()
  await expect(page.locator(".map-mode")).toContainText("Inspect")
  const spot = await trafficSpot(page)
  expect(spot).not.toBeNull()
  await page.mouse.click(spot!.x, spot!.y)
  await expect(page.locator(".maplibregl-popup")).toContainText(/Now: (no vehicles on it|\d+ vehicles?, (light|moderate|heavy) traffic)/)
  await page.getByRole("button", { name: "Inspect" }).click()
  await expect(page.locator(".maplibregl-popup")).toHaveCount(0)

  // An emergency: its vehicle gets its own marker and route
  await page.getByRole("button", { name: "Trigger Ambulance Emergency" }).click()
  await expect(mapNotice(page)).toContainText(/dispatched/i, { timeout: 15_000 })
  await expect.poll(() => drawn(page, ["emergency-vehicles"]), { timeout: 10_000 }).toBeGreaterThan(0)
  await expect.poll(() => drawn(page, ["routes-emergency"])).toBeGreaterThan(0)

  // Back to the starting view
  await page.getByRole("button", { name: "Reset view" }).click()
  await expect.poll(() => centre(page)).toBe(startingView)

  // Back to 2D keeps the same run
  await page.getByRole("button", { name: "2D" }).click()
  await expect(page.locator(".leaflet-container")).toBeVisible()
  await expect(kpi(page, "Status")).toContainText("Running")
})

test("the digital twin adds terrain, the sun's light, live weather and analysis layers", async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto("/")
  await page.getByRole("button", { name: "3D", exact: true }).click()
  await expect(map3d(page)).toHaveAttribute("data-ready", "true", { timeout: 30_000 })
  await page.getByRole("button", { name: "Digital twin", exact: true }).click()
  const twin = page.getByRole("group", { name: "Digital twin" })
  await expect(twin).toBeVisible()

  const state = () => map3d(page).evaluate((element) => {
    type Map = { getTerrain(): unknown; getSky(): Record<string, unknown> | undefined; getPixelRatio(): number }
    const map = (element as unknown as { maplibre: Map }).maplibre
    return { terrain: Boolean(map.getTerrain()), sky: map.getSky()?.["sky-color"] ?? null, ratio: map.getPixelRatio() }
  })

  // The sky follows the time of day
  await twin.getByRole("slider", { name: "Time of day" }).fill("12")
  const noon = (await state()).sky
  await twin.getByRole("slider", { name: "Time of day" }).fill("2")
  await expect.poll(async () => (await state()).sky).not.toBe(noon)

  // Weather from the backend (or its reason for having none)
  await expect(twin.getByRole("region", { name: "Weather" })).toContainText(/Open-Meteo|unavailable/, { timeout: 20_000 })

  // Population hexagons from the backend's census weights
  await twin.getByLabel("Population density (Census 2021)").check()
  await expect.poll(() => drawn(page, ["twin-population"]), { timeout: 30_000 }).toBeGreaterThan(20)

  // A viewpoint moves the camera
  const before = await centre(page)
  await twin.getByRole("button", { name: "Station and Riverside" }).click()
  await expect.poll(() => centre(page), { timeout: 10_000 }).not.toBe(before)

  // Performance mode: no terrain, standard resolution
  // Terrain, then performance mode (no terrain, standard resolution); this
  // browser draws in software, so performance mode starts on
  const lite = twin.getByLabel("Performance mode")
  await lite.uncheck()
  await twin.getByLabel("Terrain and hillshade").check()
  await expect.poll(async () => (await state()).terrain).toBe(true)
  await lite.check()
  await expect.poll(async () => (await state()).terrain).toBe(false)
  expect((await state()).ratio).toBe(1)
  await twin.getByLabel("Terrain and hillshade").isDisabled()
})
