// The analysis side on the real Norwich map: the station optimiser,
// scenario studies, saved history and the API's own documentation.
// Studies and history need the database (E2E_DATABASE_URL); without one
// the website must say so plainly instead.

import { expect, test } from "@playwright/test"

import { API_URL, clickMap, planRoute } from "./helpers.ts"

const withDatabase = Boolean(process.env.E2E_DATABASE_URL)

test("the optimiser finds station locations and a set of stations", async ({ page }) => {
  await page.goto("/")
  const panel = page.getByRole("region", { name: "Station optimiser" })

  await panel.getByLabel("Optimiser service").selectOption("fire")
  await panel.getByRole("button", { name: "Find best fire station locations" }).click()
  const results = page.getByRole("region", { name: "Optimiser results" })
  await expect(results.getByRole("heading", { name: "Single-station ranking" })).toBeVisible({ timeout: 45_000 })
  await expect(results).toContainText(/min/)
  // The candidates are drawn on the map, numbered by rank
  await expect.poll(() => page.locator(".leaflet-tooltip.marker-number").count()).toBeGreaterThanOrEqual(5)

  await panel.getByLabel("Number of stations").selectOption("3")
  await panel.getByRole("button", { name: "Find best set of 3 stations" }).click()
  await expect(results.getByRole("heading", { name: "Multi-station placement" })).toBeVisible({ timeout: 45_000 })
  await expect(results).toContainText("Recommended: 3 stations")
})

test("scenario studies compare a road closure with its baseline", async ({ page }) => {
  test.skip(!withDatabase, "Scenario studies need E2E_DATABASE_URL")
  test.setTimeout(240_000)

  await page.goto("/")
  const builder = page.getByRole("region", { name: "Scenario builder" })
  // The second scenario ("Road closure") closes a road picked on the map
  await builder.getByRole("button", { name: "Close a road" }).nth(1).click()
  // The builder is far below the map: the website brings the map into view
  await expect(page.locator(".leaflet-container")).toBeInViewport({ ratio: 0.5 })
  await clickMap(page, 0.5, 0.5)
  await builder.getByRole("button", { name: "Run 2 scenarios" }).click()

  const results = page.getByRole("region", { name: "Study results" })
  await expect(results.getByRole("table", { name: "Scenario ranking" })).toBeVisible({ timeout: 200_000 })
  await expect(results.getByRole("table", { name: "Scenario ranking" }).getByRole("row")).toHaveCount(3)
  // The closure really is in the second scenario: the two differ, and
  // trips change because of it
  const why = results.getByLabel("Why this ranking")
  await expect(why).not.toContainText("set up the same way")
  await expect(why).toContainText("1 road closure that the other does not have")
})

test("saved runs appear in the history", async ({ page }) => {
  test.skip(!withDatabase, "History needs E2E_DATABASE_URL")

  await planRoute(page)
  await page.getByRole("button", { name: "Load saved runs and studies" }).click()
  const runs = page.getByRole("table", { name: "Saved simulation runs" })
  await expect(runs).toBeVisible()
  await expect(runs.getByRole("row").nth(1)).toBeVisible()
})

test("without a database the website explains why saving is unavailable", async ({ page }) => {
  test.skip(withDatabase, "Only without a database")

  await page.goto("/")
  await page.getByRole("button", { name: "Load saved runs and studies" }).click()
  await expect(page.getByRole("alert").filter({ hasText: "DATABASE_URL is not set" })).toBeVisible()
})

test("the API documents itself and reports its health", async ({ page, request }) => {
  expect((await request.get(`${API_URL}/health`)).ok()).toBe(true)
  const ready = await (await request.get(`${API_URL}/ready`)).json()
  expect(ready.database).toBe(withDatabase ? "ok" : "not configured")

  await page.goto(`${API_URL}/docs`)
  await expect(page.getByRole("heading", { name: /NORWICH/i }).first()).toBeVisible()
  await expect(page.getByText("/simulation/start").first()).toBeVisible()
})
