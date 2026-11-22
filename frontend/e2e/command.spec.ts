// The emergency response command centre on the real map and the real
// backend: incidents reported by clicking the 3D map, units dispatched and
// drawn as they drive, timelines and figures from the simulation, and the
// recorded replay. Needs the internet for the map tiles.

import { expect, test, type Page } from "@playwright/test"

const map3d = (page: Page) => page.locator('[data-testid="map-3d"]')

async function drawn(page: Page, layers: string[]): Promise<number> {
  return map3d(page).evaluate((element, layers) => {
    const map = (element as unknown as { maplibre: { queryRenderedFeatures(o: object): unknown[] } }).maplibre
    return map.queryRenderedFeatures({ layers }).length
  }, layers)
}

async function reportAt(page: Page, kind: string, x: number, y: number) {
  await page.getByRole("region", { name: "Report an incident" }).getByRole("button", { name: kind }).click()
  const box = await map3d(page).boundingBox()
  if (!box) throw new Error("No map")
  await page.mouse.click(box.x + box.width * x, box.y + box.height * y)
}

test("the command centre dispatches units, tracks them and replays the response", async ({ page }) => {
  test.setTimeout(180_000)
  await page.goto("/")
  await page.getByRole("button", { name: "Command" }).click()
  await page.getByRole("button", { name: "Open the command centre" }).click()
  await expect(map3d(page)).toHaveAttribute("data-ready", "true", { timeout: 60_000 })
  const units = page.getByRole("region", { name: "Units" })
  await expect(units.getByText(/Fire engines: \d+ of \d+ free/)).toBeVisible()
  await expect(units.getByText(/Ambulances: 6 of 6 free/)).toBeVisible()

  // Three incidents at once
  const status = page.locator(".command-message")
  await reportAt(page, "Medical", 0.42, 0.55)
  await expect(status).toContainText(/#1 Medical emergency: 1 unit dispatched/, { timeout: 30_000 })
  await reportAt(page, "Fire", 0.6, 0.45)
  await expect(status).toContainText(/#2 Fire: 2 units dispatched/, { timeout: 30_000 })
  await reportAt(page, "Road closure", 0.5, 0.65)
  await expect(status).toContainText(/#3 Road closure: road closed/, { timeout: 30_000 })

  // Units en route, drawn with their routes
  await expect(page.getByRole("group", { name: "Response performance" })).toContainText(/3 en route/, { timeout: 15_000 })
  await expect.poll(() => drawn(page, ["routes-emergency"]), { timeout: 15_000 }).toBeGreaterThan(0)
  await expect.poll(() => drawn(page, ["emergency-vehicles", "vehicle-bodies"]), { timeout: 15_000 }).toBeGreaterThan(0)

  // Faster: the units arrive, and the timeline and figures say so
  await page.getByRole("group", { name: "Simulation speed" }).getByRole("button", { name: "30x" }).click()
  const log = page.getByRole("region", { name: "Incident log" })
  await log.getByRole("button", { name: /#1 Medical emergency/ }).click()
  const timeline = page.getByRole("region", { name: "Incident timeline" })
  await expect(timeline).toContainText(/Ambulance \d+ arrived: \d+ s/, { timeout: 60_000 })
  await expect(timeline.getByText(/within target|missed target/)).toBeVisible()
  const figures = page.getByRole("group", { name: "Response performance" })
  await expect(figures.getByText(/\d+ units? arrived/)).toBeVisible()
  await expect(figures).not.toContainText("Average response-")

  // The operator reopens the road
  await log.getByRole("button", { name: /#3 Road closure/ }).click()
  await timeline.getByRole("button", { name: "Reopen the road" }).click()
  await expect(log.getByRole("button", { name: /#3 Road closure/ })).toContainText("Resolved")

  // Replay from the recorded tracks
  await page.getByRole("region", { name: "Simulation playback" }).getByRole("button", { name: "Replay" }).click()
  const replay = page.getByRole("region", { name: "Replay" })
  await expect(replay.getByRole("slider", { name: "Replay time" })).toBeVisible()
  await replay.getByRole("button", { name: "Pause replay" }).click()
  const slider = replay.getByRole("slider", { name: "Replay time" })
  const max = Number(await slider.getAttribute("max"))
  const min = Number(await slider.getAttribute("min"))
  await slider.fill(String(Math.round(min + (max - min) * 0.1)))
  await expect.poll(() => drawn(page, ["emergency-vehicles", "vehicle-bodies"]), { timeout: 10_000 }).toBeGreaterThan(0)
  await replay.getByRole("button", { name: "Back to live" }).click()
  await expect(page.getByRole("region", { name: "Simulation playback" })).toBeVisible()
})
