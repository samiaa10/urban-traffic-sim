// The experimental cellular-automaton traffic model in the real dashboard,
// against the real backend: chosen from the route panel, it drives the
// vehicles, and the queues it produces reach the live charts and the map.

import { expect, test } from "@playwright/test"

import { clickMap, kpi, mapNotice } from "./helpers.ts"

test("a run with the cellular automaton shows its queues", async ({ page }) => {
  await page.goto("/")
  await page.getByRole("combobox", { name: "Traffic model", exact: true }).selectOption("ca")
  await clickMap(page, 0.35, 0.45)
  await clickMap(page, 0.65, 0.55)
  await page.getByRole("button", { name: "Calculate Route" }).click()
  await expect(kpi(page, "Status")).toContainText("Running", { timeout: 30_000 })

  const live = page.getByRole("region", { name: "Live controls" })
  await live.getByLabel("Fleet size").selectOption("1000")
  await live.getByRole("button", { name: "Add vehicles" }).click()
  await expect(mapNotice(page)).toContainText(/Added 1000 vehicles/)
  await live.getByLabel("Simulation speed").selectOption("10")

  // The run really uses the CA: its summary counts cars stopped in queues,
  // and the dashboard charts them
  const analytics = page.getByRole("region", { name: "Live traffic" })
  await expect(analytics.getByRole("img", { name: /Stopped in queues over/ })).toBeVisible({ timeout: 20_000 })

  // The congestion the map draws carries the CA's own queue and density
  const response = await page.waitForResponse((r) => new URL(r.url()).pathname.endsWith("/congestion") && r.ok())
  const segments = (await response.json()).congestion as { stopped?: number; occupancy?: number }[]
  // (a road holding only cars still waiting to enter has no CA state yet)
  const measured = segments.filter((s) => typeof s.stopped === "number")
  expect(measured.length).toBeGreaterThan(segments.length / 2)
  for (const s of measured) {
    expect(s.occupancy).toBeGreaterThan(0)
    expect(s.occupancy).toBeLessThanOrEqual(1)
  }
})
