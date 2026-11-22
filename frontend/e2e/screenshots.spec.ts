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

test("emergency response command centre", async ({ page }) => {
  test.setTimeout(300_000)
  const map = page.locator('[data-testid="map-3d"]')
  // Report at a real place: projected to the screen through the map
  const report = async (kind: string, latitude: number, longitude: number) => {
    await page.getByRole("region", { name: "Report an incident" }).getByRole("button", { name: kind }).click()
    await page.locator(".command-stage").evaluate((element) => element.scrollIntoView({ block: "start" }))
    const point = await map.evaluate((element, at) => {
      const live = (element as unknown as { maplibre: {
        jumpTo(view: object): void; project(p: number[]): { x: number; y: number } } }).maplibre
      live.jumpTo({ center: [1.2935, 52.6295], zoom: 13.4, pitch: 52, bearing: -15 })
      return live.project(at)
    }, [longitude, latitude])
    const box = await map.boundingBox()
    if (!box) throw new Error("No map")
    await page.mouse.click(box.x + point.x, box.y + point.y)
    await expect(page.locator(".command-message")).toContainText(`${kind === "Medical" ? "Medical emergency" : kind === "Crime" ? "Crime in progress" : kind}:`, { timeout: 30_000 })
  }
  await page.goto("/")
  await page.getByRole("button", { name: "Command" }).click()
  await page.getByRole("button", { name: "Open the command centre" }).click()
  await expect(map).toHaveAttribute("data-ready", "true", { timeout: 60_000 })
  await page.waitForTimeout(4_000)
  await report("Fire", 52.6352, 1.2868)
  await page.waitForTimeout(1_500)
  await report("Road accident", 52.6236, 1.3012)
  await page.waitForTimeout(1_500)
  await report("Medical", 52.6418, 1.2712)
  await page.waitForTimeout(1_500)
  await report("Crime", 52.6297, 1.3148)
  // Back out to see the whole response
  await map.evaluate((element) =>
    (element as unknown as { maplibre: { easeTo(view: object): void } }).maplibre.easeTo({
      center: [1.2935, 52.6295], zoom: 13.4, pitch: 52, bearing: -15, duration: 0,
    }))
  await page.getByRole("region", { name: "Incident log" }).getByRole("button", { name: /#1 Fire/ }).click()
  await page.waitForTimeout(25_000)
  await page.addStyleTag({ content: ".topbar { position: static !important; }" })
  const workspace = page.locator("#map")
  await workspace.scrollIntoViewIfNeeded()
  await workspace.screenshot({ path: `${OUT}/command-centre.jpg`, type: "jpeg", quality: 84 })

  // Replay of what was recorded
  await page.getByRole("group", { name: "Simulation speed" }).getByRole("button", { name: "30x" }).click()
  await page.waitForTimeout(20_000)
  await page.getByRole("region", { name: "Simulation playback" }).getByRole("button", { name: "Replay" }).click()
  const replay = page.getByRole("region", { name: "Replay" })
  await replay.getByRole("button", { name: "Pause replay" }).click()
  const slider = replay.getByRole("slider", { name: "Replay time" })
  const max = Number(await slider.getAttribute("max"))
  const min = Number(await slider.getAttribute("min"))
  await slider.fill(String(Math.round(min + (max - min) * 0.12)))
  await page.waitForTimeout(3_000)
  await workspace.screenshot({ path: `${OUT}/command-replay.jpg`, type: "jpeg", quality: 84 })
})

test("landing and workspace", async ({ page }) => {
  await page.goto("/")
  await expect(page.getByRole("region", { name: "About NORWICH//SIM" })).toContainText("Road network")
  await page.waitForTimeout(2_500)
  await page.screenshot({ path: `${OUT}/landing.jpg`, type: "jpeg", quality: 84 })
})

test("digital twin: light, terrain and layers", async ({ page }) => {
  test.setTimeout(180_000)
  await page.goto("/")
  await page.getByRole("button", { name: "Fold the introduction away" }).click()
  await page.getByRole("button", { name: "3D", exact: true }).click()
  const map = page.locator('[data-testid="map-3d"]')
  await expect(map).toHaveAttribute("data-ready", "true", { timeout: 60_000 })
  await page.getByRole("button", { name: "Digital twin", exact: true }).click()
  const twin = page.getByRole("group", { name: "Digital twin" })
  await twin.getByRole("slider", { name: "Time of day" }).fill("16")
  await twin.getByRole("button", { name: "City overview" }).click()
  await twin.getByLabel("Population density (Census 2021)").check()
  await page.waitForTimeout(12_000)
  const workspace = page.locator("#map")
  await workspace.scrollIntoViewIfNeeded()
  await workspace.screenshot({ path: `${OUT}/twin-population.jpg`, type: "jpeg", quality: 84 })

  await twin.getByLabel("Population density (Census 2021)").uncheck()
  await twin.getByLabel(/Emergency coverage/).check()
  await twin.getByRole("slider", { name: "Time of day" }).fill("11")
  await page.waitForTimeout(8_000)
  await workspace.screenshot({ path: `${OUT}/twin-coverage.jpg`, type: "jpeg", quality: 84 })

  // Street level at midday, with terrain and the live weather
  await twin.getByLabel(/Emergency coverage/).uncheck()
  await twin.getByRole("button", { name: "Castle and Cathedral" }).click()
  await twin.getByRole("button", { name: "Close the digital twin panel" }).click()
  await page.waitForTimeout(9_000)
  await workspace.screenshot({ path: `${OUT}/twin-castle.jpg`, type: "jpeg", quality: 84 })
})
