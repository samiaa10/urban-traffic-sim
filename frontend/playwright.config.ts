// End-to-end tests (npm run test:e2e): the built website in a real
// browser (Chromium), talking to the real backend on the real Norwich map.
// Nothing is faked, unlike the interaction tests in ui-tests/.
//
// Playwright starts both servers on ports of their own (so a backend or
// dev server already running on 8000/5173 is left alone):
//   backend   http://127.0.0.1:8002  (uvicorn, from the repository root)
//   website   http://127.0.0.1:5174  (vite preview of a fresh build)
//
// E2E_PYTHON picks the Python that runs the backend (default: the
// repository's .venv, else python3). E2E_DATABASE_URL, if set, gives the
// backend a migrated PostgreSQL database so saving and history are tested
// too; without it those tests are skipped. See e2e/README.md.

import { existsSync } from "node:fs"
import { defineConfig, devices } from "@playwright/test"

const API_PORT = 8002
const WEB_PORT = 5174
const API_URL = `http://127.0.0.1:${API_PORT}`
const WEB_URL = `http://127.0.0.1:${WEB_PORT}`

const python =
  process.env.E2E_PYTHON ?? (existsSync("../.venv/bin/python") ? ".venv/bin/python" : "python3")

export default defineConfig({
  testDir: "e2e",
  // One backend, one browser at a time: every test sees the same server
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 1000 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } }],
  webServer: [
    {
      command: `${python} -m uvicorn backend.api.main:app --host 127.0.0.1 --port ${API_PORT} --no-access-log`,
      cwd: "..",
      url: `${API_URL}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        CORS_ORIGINS: WEB_URL,
        // Tests start many runs quickly; the limit has its own tests
        RATE_LIMIT_PER_MINUTE: "0",
        SCENARIO_WORKERS: "0",
        TRAFFIC_MODEL: "osm",
        LOG_LEVEL: "WARNING",
        ...(process.env.E2E_DATABASE_URL ? { DATABASE_URL: process.env.E2E_DATABASE_URL } : {}),
      },
    },
    {
      command: `npx vite build --logLevel warn && npx vite preview --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      url: WEB_URL,
      timeout: 180_000,
      reuseExistingServer: false,
      env: { VITE_API_URL: API_URL },
    },
  ],
})
