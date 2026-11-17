// Screenshots for the README (docs/screenshots), taken from the real
// website on the real map. Not a test: runs only with SCREENSHOTS=1, and
// needs E2E_DATABASE_URL for the scenario study.
//
//   SCREENSHOTS=1 E2E_DATABASE_URL=... npx playwright test screenshots

import { expect, test } from "@playwright/test"

import { clickMap, mapNotice, planRoute } from "./helpers.ts"

const OUT = "../docs/screenshots"

test.skip(!process.env.SCREENSHOTS, "Only with SCREENSHOTS=1")
test.use({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })

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
  await page.screenshot({ path: `${OUT}/live-simulation.png` })

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
  await page.screenshot({ path: `${OUT}/emergency-response.png` })
})

test("station optimiser", async ({ page }) => {
  await page.goto("/")
  const panel = page.getByRole("region", { name: "Station optimiser" })
  await panel.getByLabel("Number of stations").selectOption("4")
  await panel.getByRole("button", { name: "Find best set of 4 stations" }).click()
  const results = page.getByRole("region", { name: "Optimiser results" })
  await expect(results.getByRole("heading", { name: "Multi-station placement" })).toBeVisible({ timeout: 60_000 })
  await page.locator("#map").scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${OUT}/station-optimiser.png` })
  await results.screenshot({ path: `${OUT}/optimiser-results.png` })
})

test("scenario study", async ({ page }) => {
  test.skip(!process.env.E2E_DATABASE_URL, "Needs E2E_DATABASE_URL")
  test.setTimeout(300_000)
  await page.goto("/")
  const builder = page.getByRole("region", { name: "Scenario builder" })
  await builder.getByRole("button", { name: "Close a road" }).first().click()
  await clickMap(page, 0.5, 0.5)
  await builder.getByRole("button", { name: "Run 2 scenarios" }).click()
  const results = page.getByRole("region", { name: "Study results" })
  await expect(results.getByRole("table", { name: "Scenario ranking" })).toBeVisible({ timeout: 280_000 })
  await results.getByRole("button", { name: /Show details of/ }).last().click()
  await page.waitForTimeout(1_500)
  await page.getByRole("region", { name: "Scenario results" }).screenshot({ path: `${OUT}/scenario-study.png` })
})
