// Interaction tests (npm run test:ui): render the real App in jsdom and
// click through it. See ui-tests/helpers.ts and ui-tests/fakeLeaflet.tsx.
// The logic tests in tests/ use Node's own test runner instead
// (npm run test:unit).

import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["ui-tests/**/*.test.tsx"],
    setupFiles: ["./ui-tests/setup.ts"],
  },
})
