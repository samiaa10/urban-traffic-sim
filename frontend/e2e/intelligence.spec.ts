// The City Analyst, natural-language scenarios and the insights panel on
// the real backend. The test servers have no AI key, so the built-in rules
// answer: the same calculations, worded by templates.

import { expect, test } from "@playwright/test"

test("the analyst answers from calculations and says who wrote the answer", async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto("/")
  await page.getByRole("button", { name: "Analyst", exact: true }).click()
  const analyst = page.getByRole("dialog", { name: "City Analyst" })
  await expect(analyst).toContainText("Built-in rules (no AI model configured)")

  await analyst.getByRole("button", { name: "Which areas have the worst emergency response coverage?" }).click()
  await expect(analyst.getByText("Calculated (built-in rules)")).toBeVisible({ timeout: 60_000 })
  await expect(analyst).toContainText(/% of residents are within 4 min/)
  await analyst.getByText(/1 calculation used/).click()
  await expect(analyst).toContainText("Emergency coverage gaps")

  await analyst.getByLabel("Your question").fill("What would happen if the central bridges closed?")
  await analyst.getByRole("button", { name: "Ask", exact: true }).click()
  await expect(analyst).toContainText(/Closing Duke Street, Fye Bridge Street, Prince of Wales Road, Whitefriars/, { timeout: 60_000 })
})

test("a scenario described in words is proposed, confirmed and run", async ({ page }) => {
  test.setTimeout(150_000)
  await page.goto("/")
  await page.getByRole("button", { name: "Analyst", exact: true }).click()
  const analyst = page.getByRole("dialog", { name: "City Analyst" })
  await analyst.getByRole("tab", { name: "Create a scenario" }).click()
  await analyst.getByLabel("Describe a scenario").fill("Close a major bridge and measure the consequences.")
  await analyst.getByRole("button", { name: "Propose a scenario" }).click()

  const proposal = analyst.getByRole("region", { name: "Proposed scenario" })
  await expect(proposal).toContainText("Close the central bridges", { timeout: 60_000 })
  await expect(proposal).toContainText("Nothing has run yet")
  const started = page.waitForResponse((r) => r.url().includes("/intelligence/scenario/run"))
  await proposal.getByRole("button", { name: "Run this scenario" }).click()
  const run = await (await started).json()
  expect(run.impact.roads_closed).toBeGreaterThan(0)

  const running = analyst.getByRole("region", { name: "Scenario running" })
  await expect(running).toContainText(/Calculated: the \d+ closed road segment\(s\) make/)
  await running.getByRole("button", { name: "Watch it in the command centre" }).click()
  await expect(page.locator('[data-testid="map-3d"]')).toHaveAttribute("data-ready", "true", { timeout: 60_000 })
  await expect(page.getByRole("region", { name: "Units" })).toContainText(/Fire engines: \d+ of \d+ free/, { timeout: 20_000 })
  const incidents = await page.request.get(`http://127.0.0.1:8002/simulations/${run.simulation_id}/incidents`)
  expect((await incidents.json()).incidents.length).toBe(run.impact.roads_closed)
})

test("the insights panel shows calculated coverage, stations and critical roads", async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto("/#insights")
  const panel = page.locator("#insights")
  await panel.getByRole("button", { name: "Run the analysis" }).click()
  await expect(panel.getByRole("article", { name: "Fire coverage" })).toContainText(/Recommendation: one more fire station/, { timeout: 90_000 })
  await expect(panel.getByRole("article", { name: "Critical roads" })).toContainText(/% of trips/)
  await expect(panel.getByRole("article", { name: "Live simulation" })).toBeVisible()
})
