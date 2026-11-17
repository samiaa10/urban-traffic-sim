// The live simulation on the real Norwich map: planning a route, the live
// controls, incidents, emergency services and live analytics.

import { expect, test } from "@playwright/test"

import { clickMap, kpi, mapNotice, planRoute, simulationTime } from "./helpers.ts"

test("plans a route across Norwich and simulates it live", async ({ page }) => {
  await planRoute(page)

  await expect(kpi(page, "Status")).toContainText("Running")
  await expect(kpi(page, "Route length")).toContainText(/A\*, [\d,]+ nodes explored/)
  // The planned route and the vehicles are drawn on the map
  await expect(page.locator(".leaflet-overlay-pane path").first()).toBeVisible()

  // Time moves on by itself, and the live charts fill in
  const first = await simulationTime(page)
  await expect.poll(() => simulationTime(page)).toBeGreaterThan(first + 1)
  const analytics = page.getByRole("region", { name: "Live traffic" })
  await expect(analytics.getByRole("status")).toContainText(/\d+ driving, \d+ held up/)
  await expect(analytics.getByRole("img", { name: /Vehicles driving/ })).toBeVisible()
})

test("pauses, resumes and runs faster", async ({ page }) => {
  await planRoute(page)
  const live = page.getByRole("region", { name: "Live controls" })

  await live.getByRole("button", { name: "Pause" }).click()
  const paused = await simulationTime(page)
  await page.waitForTimeout(2500)
  expect(await simulationTime(page)).toBe(paused)

  await live.getByLabel("Simulation speed").selectOption("10")
  await live.getByRole("button", { name: "Resume" }).click()
  // Ten simulated seconds a tick: well past 10 s within a few ticks
  await expect.poll(() => simulationTime(page), { timeout: 10_000 }).toBeGreaterThanOrEqual(paused + 20)
})

test("adds a fleet and a rush hour, and the network feels it", async ({ page }) => {
  await planRoute(page)
  const live = page.getByRole("region", { name: "Live controls" })

  await live.getByLabel("Fleet size").selectOption("500")
  await live.getByRole("button", { name: "Add vehicles" }).click()
  await expect(mapNotice(page)).toContainText(/Added 500 vehicles/)
  await expect.poll(async () => Number((await kpi(page, "Vehicles").locator(".kpi-value").innerText()).replace(/\D/g, "")))
    .toBeGreaterThan(400)

  await live.getByRole("button", { name: "Start a rush hour into the centre" }).click()
  await expect(mapNotice(page)).toContainText("Rush hour")
  await expect(kpi(page, "Traffic flow")).toContainText(/of free-flow speed, \d+ of \d+ held up/)
})

test("adds an accident and a closure on roads picked on the map, then clears them", async ({ page }) => {
  await planRoute(page)
  const live = page.getByRole("region", { name: "Live controls" })

  await live.getByLabel("Accident severity").selectOption("moderate")
  await live.getByRole("button", { name: "Add an accident" }).click()
  await clickMap(page, 0.5, 0.5)
  await expect(mapNotice(page)).toContainText(/Moderate accident on .+ for 15 min/)

  await live.getByRole("button", { name: "Close a road" }).click()
  await clickMap(page, 0.45, 0.4)
  await expect(mapNotice(page)).toContainText(/Closed .+: vehicles that were using it re-plan/)

  const incidents = live.getByRole("list", { name: "Your incidents" })
  await expect(incidents.getByRole("listitem")).toHaveCount(2)

  await incidents.getByRole("button", { name: "Clear" }).click()
  await expect(mapNotice(page)).toContainText("Cleared the accident on")
  await incidents.getByRole("button", { name: "Reopen" }).click()
  await expect(mapNotice(page)).toContainText("Reopened")
  await expect(incidents).toHaveCount(0)

  await live.getByLabel("Dynamic rerouting").uncheck()
  await expect(mapNotice(page)).toContainText("Dynamic rerouting off")
})

test("closes a road on the route and reroutes", async ({ page }) => {
  await planRoute(page)

  await page.getByRole("button", { name: "Cause Road Closure & Reroute" }).click()

  await expect(mapNotice(page)).toContainText(/Road closed: \d+ of \d+ active vehicles used it, \d+ rerouted/)
  await expect(mapNotice(page)).not.toHaveClass(/notice--error/)
})

const SERVICES = [
  ["ambulance", "Ambulance", "Ambulance"],
  ["fire", "Fire", "Fire engine"],
  ["police", "Police", "Police car"],
] as const

for (const [service, button, label] of SERVICES) {
  test(`sends the ${label.toLowerCase()} to an emergency at the destination`, async ({ page }) => {
    await planRoute(page)

    await page.getByLabel("Emergency service").selectOption(service)
    await page.getByRole("button", { name: `Trigger ${button} Emergency` }).click()

    await expect(mapNotice(page)).toContainText(
      new RegExp(`${label} \\d+ dispatched \\| \\d+ m response route cost \\| \\d+ nodes explored`))
    await expect(mapNotice(page)).not.toHaveClass(/notice--error/)
  })
}

test("visitors never replace each other's runs", async ({ browser }) => {
  const first = await browser.newPage()
  const second = await browser.newPage()

  await planRoute(first)
  const before = await simulationTime(first)
  await planRoute(second, [0.4, 0.6], [0.6, 0.4])

  // The first visitor's simulation carries on, unaffected
  await expect.poll(() => simulationTime(first)).toBeGreaterThan(before + 2)
  await expect(kpi(first, "Status")).toContainText("Running")
  await expect(kpi(second, "Status")).toContainText("Running")

  await first.close()
  await second.close()
})

test("says why when no route reaches the chosen destination", async ({ page }) => {
  await page.goto("/")
  await clickMap(page, 0.3, 0.4)
  // By the river east of the city: its nearest junction cannot be reached
  await clickMap(page, 0.7, 0.6)
  await page.getByRole("button", { name: "Calculate Route" }).click()

  await expect(mapNotice(page)).toContainText("Could not calculate route: No route available")
  await expect(mapNotice(page)).not.toContainText("Is the backend running?")
})

test("keeps up with a busy city: 2,000 vehicles and a rush hour at 5x", async ({ page }) => {
  test.setTimeout(90_000)
  await planRoute(page)
  const live = page.getByRole("region", { name: "Live controls" })
  await live.getByLabel("Fleet size").selectOption("2000")
  await live.getByRole("button", { name: "Add vehicles" }).click()
  await expect(mapNotice(page)).toContainText("Added 2000 vehicles", { timeout: 30_000 })
  await live.getByRole("button", { name: "Start a rush hour into the centre" }).click()
  await live.getByLabel("Simulation speed").selectOption("5")

  // A step of this run takes longer than a tick: the page must still move
  // on steadily (it used to queue steps and freeze)
  const start = await simulationTime(page)
  await expect.poll(() => simulationTime(page), { timeout: 30_000 }).toBeGreaterThanOrEqual(start + 25)
})
