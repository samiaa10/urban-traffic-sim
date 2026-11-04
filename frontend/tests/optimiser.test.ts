// Tests for how the frontend talks to the optimiser endpoints
// (src/optimiser.ts). fetch is replaced by a fake, so no backend is needed.

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import {
  API,
  count,
  describeFailure,
  fetchRanking,
  fetchStationSet,
  minutes,
  percent,
  place,
  requestJson,
  type FetchLike,
} from "../src/optimiser.ts"

// A fake fetch that remembers what it was asked for
function fakeFetch(status: number, body: unknown, asText = false) {
  const urls: string[] = []

  const fetchImpl: FetchLike = async (url) => {
    urls.push(url)

    return new Response(asText ? String(body) : JSON.stringify(body), {
      status,
    })
  }

  return { fetchImpl, urls }
}

describe("formatting", () => {
  it("minutes", () => {
    assert.equal(minutes(150), "2.5 min")
    assert.equal(minutes(0), "0.0 min")
    assert.equal(minutes(480), "8.0 min")
  })

  it("minutes shows n/a when there is no value", () => {
    assert.equal(minutes(null), "n/a")
  })

  it("percent keeps one decimal, so 99.6% is not shown as 100%", () => {
    assert.equal(percent(99.6), "99.6%")
    assert.equal(percent(100), "100.0%")
    assert.equal(percent(0), "0.0%")
  })

  it("count keeps whole numbers whole and averages to one decimal", () => {
    assert.equal(count(2), "2")
    assert.equal(count(0), "0")
    assert.equal(count(1.88), "1.9")
  })

  it("place", () => {
    assert.equal(place(52.63567, 1.27321), "52.6357, 1.2732")
  })
})

describe("requestJson", () => {
  it("returns the body of a successful response", async () => {
    const { fetchImpl, urls } = fakeFetch(200, { hello: "world" })

    const body = await requestJson<{ hello: string }>("/x", fetchImpl)

    assert.deepEqual(body, { hello: "world" })
    assert.deepEqual(urls, [`${API}/x`])
  })

  it("uses the backend's own explanation when it gives one", async () => {
    const { fetchImpl } = fakeFetch(400, {
      detail: "stations must be between 1 and 10",
    })

    await assert.rejects(
      requestJson("/x", fetchImpl),
      { message: "stations must be between 1 and 10" }
    )
  })

  it("does not show FastAPI's validation-error list to the user", async () => {
    const { fetchImpl } = fakeFetch(422, {
      detail: [{ loc: ["query", "stations"], msg: "value is not a valid integer" }],
    })

    await assert.rejects(requestJson("/x", fetchImpl), {
      message: "The optimiser failed",
    })
  })

  it("falls back when an error response has no detail", async () => {
    const { fetchImpl } = fakeFetch(500, {})

    await assert.rejects(requestJson("/x", fetchImpl), {
      message: "The optimiser failed",
    })
  })

  it("falls back when an error response is not JSON", async () => {
    const { fetchImpl } = fakeFetch(502, "<html>Bad gateway</html>", true)

    await assert.rejects(requestJson("/x", fetchImpl), {
      message: "The optimiser failed",
    })
  })

  it("copes with an error body of null", async () => {
    const { fetchImpl } = fakeFetch(500, null)

    await assert.rejects(requestJson("/x", fetchImpl), {
      message: "The optimiser failed",
    })
  })

  it("reports a successful response that is not JSON", async () => {
    const { fetchImpl } = fakeFetch(200, "not json", true)

    await assert.rejects(requestJson("/x", fetchImpl), {
      message: "The server sent an unreadable answer",
    })
  })

  it("lets a network failure through (backend not running)", async () => {
    const fetchImpl: FetchLike = async () => {
      throw new TypeError("Failed to fetch")
    }

    await assert.rejects(requestJson("/x", fetchImpl), {
      message: "Failed to fetch",
    })
  })
})

describe("the optimiser requests", () => {
  it("asks for the one-station ranking with 500 scenarios", async () => {
    const { fetchImpl, urls } = fakeFetch(200, { results: [] })

    await fetchRanking(fetchImpl)

    assert.deepEqual(urls, [`${API}/optimiser/stations?scenarios=500`])
  })

  it("asks for a station set, converting the target to seconds", async () => {
    const { fetchImpl, urls } = fakeFetch(200, { stations: [] })

    await fetchStationSet(3, 8, fetchImpl)
    await fetchStationSet(5, 4, fetchImpl)

    assert.deepEqual(urls, [
      `${API}/optimiser/station-set?stations=3&scenarios=500&target_seconds=480`,
      `${API}/optimiser/station-set?stations=5&scenarios=500&target_seconds=240`,
    ])
  })

  it("passes a station-set error message on", async () => {
    const { fetchImpl } = fakeFetch(400, {
      detail: "Asked for 5 stations but only 2 distinct candidate junctions are available.",
    })

    await assert.rejects(fetchStationSet(5, 8, fetchImpl), {
      message: /only 2 distinct/,
    })
  })
})

describe("describeFailure", () => {
  it("shows the error message", () => {
    assert.equal(
      describeFailure(new Error("Failed to fetch")),
      "Optimiser failed: Failed to fetch"
    )
  })

  it("copes with something that is not an Error", () => {
    assert.equal(describeFailure("oops"), "Optimiser failed.")
    assert.equal(describeFailure(undefined), "Optimiser failed.")
  })
})
