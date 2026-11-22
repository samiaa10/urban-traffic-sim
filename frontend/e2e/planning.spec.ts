// The planning view on the real map: GET /optimiser/plan calculates
// road-network response times for Norwich, and the 3D map draws them as
// hexagon columns with the stations on top. Needs the internet for the
// map tiles, as the 3D map does.

import { expect, test, type Page } from "@playwright/test"

const planningMap = (page: Page) => page.locator('[data-testid="planning-map"]')

// How many features of the given layers the planning map has on screen
async function drawn(page: Page, layers: string[]): Promise<number> {
  return planningMap(page).evaluate((element, layers) => {
    const map = (element as unknown as { maplibre: { queryRenderedFeatures(o: object): unknown[] } }).maplibre
    return map.queryRenderedFeatures({ layers }).length
  }, layers)
}

// The kinds of station column drawn
async function columnKinds(page: Page): Promise<string[]> {
  return planningMap(page).evaluate((element) => {
    type Map = { getSource(id: string): { serialize(): { data: { features: { properties: { kind: string } }[] } } } }
    const map = (element as unknown as { maplibre: Map }).maplibre
    return [...new Set(map.getSource("plan-columns").serialize().data.features.map((f) => f.properties.kind))].sort()
  })
}

test("the planning view maps response times and plays the optimisation", async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto("/")
  const answer = page.waitForResponse((r) => r.url().includes("/optimiser/plan") && r.ok(), { timeout: 60_000 })
  await page.getByRole("button", { name: "Plan" }).click()
  const plan = await (await answer).json()
  await expect(planningMap(page)).toHaveAttribute("data-ready", "true", { timeout: 30_000 })
  await expect(page.locator(".leaflet-container")).toHaveCount(0)

  // The figures are the backend's, for the default settings
  expect(plan.service).toBe("fire")
  expect(plan.target_seconds).toBe(240)
  expect(plan.existing).toHaveLength(3)
  const table = page.getByRole("table", { name: "Existing and optimised layouts" })
  const reached = (s: { summary: { population_within_target_percent: number } }) =>
    `${s.summary.population_within_target_percent.toFixed(1)}%`
  const row = table.getByRole("row", { name: /Residents within 4 min/ })
  await expect(row).toContainText(reached(plan.stages[0]))
  await expect(row).toContainText(reached(plan.stages.at(-1)))

  // Hexagon columns, buildings and stations are drawn
  await expect.poll(() => drawn(page, ["plan-coverage-3d"]), { timeout: 20_000 }).toBeGreaterThan(100)
  // (at zoom 13 the tiles merge buildings into a few large features)
  await expect.poll(() => drawn(page, ["buildings-3d"]), { timeout: 20_000 }).toBeGreaterThan(0)
  await expect.poll(() => columnKinds(page)).toEqual(["existing", "proposed"])

  // Flat hexagons instead of columns
  const layers = page.getByRole("group", { name: "Layers" })
  await layers.getByLabel("3D columns").uncheck()
  await expect.poll(() => drawn(page, ["plan-coverage-flat"])).toBeGreaterThan(100)
  expect(await drawn(page, ["plan-coverage-3d"])).toBe(0)
  await layers.getByLabel("3D columns").check()

  // Step through the optimisation: the candidates, then the first pick
  const next = page.getByRole("button", { name: "Next step" })
  const step = page.getByRole("region", { name: "Optimisation step" })
  await next.click()
  await expect(step).toContainText("Existing fire stations")
  await next.click()
  await expect(step).toContainText(`${plan.search.candidates} candidate sites`)
  await expect.poll(() => columnKinds(page)).toEqual(["candidate", "existing"])
  await next.click()
  await next.click()
  await expect(step).toContainText("Station 1 of 3")
  await expect.poll(() => columnKinds(page)).toEqual(["existing", "proposed"])

  // Another service and a disruption ask the backend again
  const again = page.waitForResponse((r) => r.url().includes("service=police") && r.url().includes("disruption=flood"), { timeout: 60_000 })
  const controls = page.getByRole("region", { name: "Planning controls" })
  await controls.getByRole("combobox", { name: "Service" }).selectOption("police")
  await controls.getByRole("combobox", { name: "Disruption" }).selectOption("flood")
  const flooded = await (await again).json()
  expect(flooded.closed_roads).toBeGreaterThan(0)
  await expect(page.getByText("Police: existing against optimised")).toBeVisible()
  await expect(page.getByText(`${flooded.closed_roads} roads closed.`, { exact: false })).toBeVisible()

  // The difference view
  await page.getByRole("group", { name: "Layout shown" }).getByRole("button", { name: "Difference" }).click()
  await expect(page.getByText("Time saved by the optimised layout")).toBeVisible()
  await expect.poll(() => drawn(page, ["plan-coverage-3d"])).toBeGreaterThan(100)
})
