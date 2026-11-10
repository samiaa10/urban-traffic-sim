import assert from "node:assert/strict"
import { afterEach, describe, it } from "node:test"

import { API, requestJson, setApiBase } from "../src/optimiser.ts"

function recordUrls() {
  const urls: string[] = []

  const fakeFetch = async (url: string) => {
    urls.push(url)
    return { ok: true, json: async () => ({}) } as Response
  }

  return { urls, fakeFetch }
}

describe("backend address", () => {
  // Put the default back so other tests are not affected
  afterEach(() => setApiBase(API))

  it("uses the local backend unless told otherwise", async () => {
    const { urls, fakeFetch } = recordUrls()

    await requestJson("/x", fakeFetch)

    assert.deepEqual(urls, ["http://127.0.0.1:8000/x"])
  })

  it("uses the deployed backend once it is set", async () => {
    const { urls, fakeFetch } = recordUrls()

    setApiBase("https://norwich-sim-api.onrender.com")
    await requestJson("/optimiser/stations", fakeFetch)

    assert.deepEqual(urls, [
      "https://norwich-sim-api.onrender.com/optimiser/stations",
    ])
  })

  it("ignores a trailing slash so URLs never contain //", async () => {
    const { urls, fakeFetch } = recordUrls()

    setApiBase("https://norwich-sim-api.onrender.com/")
    await requestJson("/x", fakeFetch)

    assert.deepEqual(urls, ["https://norwich-sim-api.onrender.com/x"])
  })
})
