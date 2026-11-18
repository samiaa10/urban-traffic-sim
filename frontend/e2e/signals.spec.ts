// Traffic signals in the real dashboard against the real backend: switched
// on from the live controls, they run at the junctions OpenStreetMap shows
// with traffic lights, and the map draws their real state.

import { expect, test } from "@playwright/test"

import { mapNotice, planRoute } from "./helpers.ts"

test("adaptive signals switch on and the map shows their lights", async ({ page }) => {
  await planRoute(page)
  const live = page.getByRole("region", { name: "Live controls" })
  await live.getByLabel("Fleet size").selectOption("500")
  await live.getByRole("button", { name: "Add vehicles" }).click()
  await expect(mapNotice(page)).toContainText(/Added 500 vehicles/)

  await live.getByRole("combobox", { name: "Signal control" }).selectOption("adaptive")
  await live.getByRole("checkbox", { name: "Emergency vehicle priority" }).check()
  await live.getByRole("button", { name: "Apply signals" }).click()
  await expect(mapNotice(page)).toContainText("Adaptive signals at 108 junctions, with emergency priority")

  // The dashboard reads the signals' state every tick
  const response = await page.waitForResponse((r) => r.url().endsWith("/traffic/signals") && r.request().method() === "GET")
  const state = await response.json()
  expect(state.controller).toBe("adaptive")
  expect(state.junctions).toHaveLength(108)
  const lights = state.junctions.flatMap((j: { approaches: { light: string }[] }) => j.approaches.map((a) => a.light))
  expect(lights).toContain("green")
  expect(lights).toContain("red")
  await expect(page.getByText("Signal red")).toBeVisible()
})
