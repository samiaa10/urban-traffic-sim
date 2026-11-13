// Runs around every interaction test (see vitest.config.ts).

import { cleanup } from "@testing-library/react"
import { afterEach, vi } from "vitest"

import { resetMap } from "./mapState.ts"

afterEach(() => {
  // Unmount first: the app's clean-up clears its polling timer, which
  // must still reach the fake timer from controlPolling()
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  resetMap()
})
