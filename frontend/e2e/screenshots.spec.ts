// Screenshots for the README (docs/screenshots), taken from the real
// website on the real map. Not a test: runs only with SCREENSHOTS=1, and
// needs E2E_DATABASE_URL for the scenario study.
//
//   SCREENSHOTS=1 E2E_DATABASE_URL=... npx playwright test screenshots

import { expect, test, type Page } from "@playwright/test"

import { clickMap, mapNotice, planRoute } from "./helpers.ts"

const OUT = "../docs/screenshots"

test.skip(!process.env.SCREENSHOTS, "Only with SCREENSHOTS=1")
test.use({
  viewport: { width: 1440, height: 1000 },
  deviceScaleFactor: 2,
  // The GPU where there is one (Apple silicon), for sharper 3D shots;
  // Chromium falls back to software rendering elsewhere
  launchOptions: { args: ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"] },
})

const map3d = (page: Page) => page.locator('[data-testid="map-3d"]')

test("operations view in 3D", async ({ page }) => {
  test.setTimeout(180_000)
  await planRoute(page, [0.3, 0.4], [0.65, 0.55])
  const live = page.getByRole("region", { name: "Live controls" })
  await live.getByLabel("Fleet size").selectOption("2000")
  await live.getByRole("button", { name: "Add vehicles" }).click()
  await live.getByRole("button", { name: "Start a rush hour into the centre" }).click()
  await live.getByLabel("Simulation speed").selectOption("5")
  await page.waitForTimeout(30_000)
  await page.getByRole("button", { name: "Trigger Ambulance Emergency" }).click()
  await expect(mapNotice(page)).toContainText("dispatched")
  await page.getByRole("button", { name: "3D", exact: true }).click()
  await expect(map3d(page)).toHaveAttribute("data-ready", "true", { timeout: 30_000 })
  await page.waitForTimeout(10_000)
  await page.screenshot({ path: `${OUT}/operations-3d.jpg`, type: "jpeg", quality: 82 })

  // Close up: every vehicle a body on the real shape of its road
  await map3d(page).evaluate((element) =>
    (element as unknown as { maplibre: { jumpTo(view: object): void } }).maplibre.jumpTo({
      center: [1.2985, 52.6285], zoom: 17, pitch: 60, bearing: -30,
    }))
  await page.waitForTimeout(6_000)
  await page.locator("#map").screenshot({ path: `${OUT}/vehicles-3d.jpg`, type: "jpeg", quality: 82 })
})

test("live simulation", async ({ page }) => {
  test.setTimeout(120_000)
  await planRoute(page, [0.3, 0.4], [0.65, 0.55])
  const live = page.getByRole("region", { name: "Live controls" })
  await live.getByLabel("Fleet size").selectOption("2000")
  await live.getByRole("button", { name: "Add vehicles" }).click()
  await live.getByRole("button", { name: "Start a rush hour into the centre" }).click()
  await live.getByLabel("Simulation speed").selectOption("5")
  await live.getByRole("button", { name: "Add an accident" }).click()
  await clickMap(page, 0.52, 0.5)
  await expect(mapNotice(page)).toContainText("accident on")
  // Let congestion build up
  await page.waitForTimeout(40_000)
  await page.screenshot({ path: `${OUT}/live-simulation.jpg`, type: "jpeg", quality: 82 })

  const analytics = page.getByRole("region", { name: "Live traffic" })
  await analytics.scrollIntoViewIfNeeded()
  await analytics.screenshot({ path: `${OUT}/live-analytics.png` })
})

test("emergency response", async ({ page }) => {
  await planRoute(page, [0.25, 0.3], [0.6, 0.65])
  await page.getByLabel("Emergency service").selectOption("fire")
  await page.getByRole("button", { name: "Trigger Fire Emergency" }).click()
  await expect(mapNotice(page)).toContainText("dispatched")
  await page.waitForTimeout(6_000)
  await page.screenshot({ path: `${OUT}/emergency-response.jpg`, type: "jpeg", quality: 82 })
})

test("station optimiser", async ({ page }) => {
  await page.goto("/")
  const panel = page.getByRole("region", { name: "Station optimiser" })
  await panel.getByLabel("Number of stations").selectOption("4")
  await panel.getByRole("button", { name: "Find best set of 4 stations" }).click()
  const results = page.getByRole("region", { name: "Optimiser results" })
  await expect(results.getByRole("heading", { name: "Multi-station placement" })).toBeVisible({ timeout: 60_000 })
  await page.locator("#map").scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${OUT}/station-optimiser.jpg`, type: "jpeg", quality: 82 })
  await results.screenshot({ path: `${OUT}/optimiser-results.png` })
})

test("scenario study", async ({ page }) => {
  test.skip(!process.env.E2E_DATABASE_URL, "Needs E2E_DATABASE_URL")
  test.setTimeout(600_000)
  await page.goto("/")
  const builder = page.getByRole("region", { name: "Scenario builder" })
  // A Monte Carlo study: the baseline and 20 sets of 3 random closures
  await builder.getByText("Large study: random closures").click()
  await builder.getByRole("button", { name: "Run random-closure study" }).click()
  const results = page.getByRole("region", { name: "Study results" })
  await expect(results.getByText("completed", { exact: true })).toBeVisible({ timeout: 580_000 })
  await page.addStyleTag({ content: ".topbar { position: static !important; }" })
  await results.screenshot({ path: `${OUT}/scenario-study.png` })
})

test("emergency infrastructure planning in 3D", async ({ page }) => {
  test.setTimeout(240_000)
  const planningMap = page.locator('[data-testid="planning-map"]')
  const settle = async () => {
    await expect(page.getByText("Calculating on the road network…")).toHaveCount(0, { timeout: 60_000 })
    await page.waitForTimeout(4_000)
  }
  await page.goto("/")
  await page.getByRole("button", { name: "Plan" }).click()
  await expect(planningMap).toHaveAttribute("data-ready", "true", { timeout: 30_000 })
  await settle()
  await page.addStyleTag({ content: ".topbar { position: static !important; }" })
  const workspace = page.locator("#map")
  await workspace.scrollIntoViewIfNeeded()
  await workspace.screenshot({ path: `${OUT}/planning-optimised.jpg`, type: "jpeg", quality: 84 })

  const views = page.getByRole("group", { name: "Layout shown" })
  await views.getByRole("button", { name: "Existing" }).click()
  await page.waitForTimeout(2_500)
  await workspace.screenshot({ path: `${OUT}/planning-existing.jpg`, type: "jpeg", quality: 84 })

  await views.getByRole("button", { name: "Difference" }).click()
  await page.waitForTimeout(2_500)
  await workspace.screenshot({ path: `${OUT}/planning-difference.jpg`, type: "jpeg", quality: 84 })

  // A step of the optimisation: the most promising single sites
  const next = page.getByRole("button", { name: "Next step" })
  for (let i = 0; i < 3; i++) await next.click()
  await page.waitForTimeout(3_000)
  await workspace.screenshot({ path: `${OUT}/planning-step.jpg`, type: "jpeg", quality: 84 })
})
