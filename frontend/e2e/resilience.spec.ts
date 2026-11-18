// A flood in the real dashboard against the real backend: placed on the
// map, it closes real roads, vehicles reroute, and accessibility drops.

import { expect, test } from "@playwright/test"

import { clickMap, mapNotice, planRoute } from "./helpers.ts"

test("a flood placed on the map closes roads and the network becomes harder to cross", async ({ page }) => {
  await planRoute(page)
  const live = page.getByRole("region", { name: "Live controls" })
  await live.getByRole("combobox", { name: "Flood radius" }).selectOption("400")
  await live.getByRole("button", { name: "Place a flood on the map" }).click()
  await clickMap(page, 0.5, 0.5)
  await expect(mapNotice(page)).toContainText(/Flood: \d+ roads closed for 20 min/, { timeout: 15_000 })
  await expect(live.getByRole("list", { name: "Floods" })).toContainText(/roads closed/)

  const resilience = page.getByRole("region", { name: "Network resilience" })
  await resilience.getByRole("button", { name: "Measure accessibility now" }).click()
  const table = resilience.getByRole("table", { name: "Accessibility before and now" })
  await expect(table).toBeVisible({ timeout: 20_000 })
  const row = table.getByRole("row", { name: /Mean extra drive time/ })
  await expect(row).not.toContainText(/^Mean extra drive time-0 s$/)
  await expect(resilience.getByRole("status")).toContainText(/[1-9]\d* roads closed/)
})
