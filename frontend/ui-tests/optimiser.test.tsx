// The emergency station optimiser as a person uses it: running a search,
// changing the number of stations and the target time, waiting, reading
// results, clearing them, and seeing errors. The optimiser panel is part
// of the main screen, so these tests render the whole app.

import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import App from "../src/App.tsx"
import { COLOURS } from "../src/mapStyles.ts"
import {
  CHOSEN_STATION,
  RANKED_STATION,
  deferred,
  markerNumbers,
  markers,
  mockBackend,
  rankingAnswer,
  stationSetAnswer,
  workingBackend,
  type Reply,
} from "./helpers.ts"

vi.mock("react-leaflet", async () => await import("./fakeLeaflet.tsx"))

const rankingButton = () =>
  screen.getByRole("button", { name: /Find best ambulance station locations/ }) as HTMLButtonElement

const setButton = () =>
  screen.getByRole("button", { name: /Find best set of \d stations/ }) as HTMLButtonElement

const clearButton = () => screen.queryByRole("button", { name: "Clear results" })

describe("ranking single stations", () => {
  it("asks the backend for a ranking and shows the table and numbered markers", async () => {
    const backend = mockBackend({
      ...workingBackend(),
      "GET /optimiser/stations": { body: rankingAnswer(3) },
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(rankingButton())

    expect(await screen.findByText(/Recommended: candidate #1/)).toBeTruthy()
    expect(backend.last("GET /optimiser/stations")?.query.get("scenarios")).toBe("500")
    expect(screen.getByText("Rank")).toBeTruthy()
    expect(markerNumbers(RANKED_STATION)).toEqual(["1", "2", "3"])
    // Rank 1 is drawn in green so it stands out
    expect(markers(RANKED_STATION)[0].getAttribute("data-color")).toBe(COLOURS.rankBest)
    expect(markers(RANKED_STATION)[1].getAttribute("data-color")).toBe(COLOURS.rank)
  })

  it("shows a loading message and disables both buttons while waiting", async () => {
    const answer = deferred<Reply>()
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/stations": () => answer.promise,
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(rankingButton())

    expect(screen.getByText(/Running 500 random emergencies/)).toBeTruthy()
    expect(rankingButton().disabled).toBe(true)
    expect(setButton().disabled).toBe(true)

    await act(async () => {
      answer.resolve({ body: rankingAnswer(2) })
    })

    expect(await screen.findByText(/Recommended: candidate #1/)).toBeTruthy()
    expect(screen.queryByText(/Running 500 random emergencies/)).toBeNull()
    expect(rankingButton().disabled).toBe(false)
    expect(setButton().disabled).toBe(false)
  })

  it("says so when no candidate stations were found", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/stations": { body: rankingAnswer(0) },
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(rankingButton())

    expect(await screen.findByText("No candidate stations were found.")).toBeTruthy()
    expect(markers(RANKED_STATION)).toHaveLength(0)
  })
})

describe("choosing the best set of stations", () => {
  it("starts with 3 stations and an 8 minute target", async () => {
    const backend = mockBackend({
      ...workingBackend(),
      "GET /optimiser/station-set": { body: stationSetAnswer(3) },
    })
    const user = userEvent.setup()
    render(<App />)

    expect(setButton().textContent).toMatch(/best set of 3 stations/)

    await user.click(setButton())
    await screen.findByText(/Recommended: 3 stations/)

    const query = backend.last("GET /optimiser/station-set")?.query
    expect(query?.get("stations")).toBe("3")
    expect(query?.get("target_seconds")).toBe("480")
  })

  it("sends the chosen number of stations and target time", async () => {
    const backend = mockBackend({
      ...workingBackend(),
      "GET /optimiser/station-set": { body: stationSetAnswer(4) },
    })
    const user = userEvent.setup()
    render(<App />)

    await user.selectOptions(screen.getByLabelText("Number of stations"), "4")
    await user.selectOptions(screen.getByLabelText("Target response time"), "5")

    // The button and the options follow what was chosen
    expect(setButton().textContent).toMatch(/best set of 4 stations/)

    await user.click(setButton())
    await screen.findByText(/Recommended: 4 stations/)

    const query = backend.last("GET /optimiser/station-set")?.query
    expect(query?.get("stations")).toBe("4")
    expect(query?.get("scenarios")).toBe("500")
    expect(query?.get("target_seconds")).toBe("300")
  })

  it("offers 2 to 5 stations and 3, 4, 5 or 8 minute targets", () => {
    mockBackend(workingBackend())
    render(<App />)

    const options = (label: string) =>
      Array.from(
        (screen.getByLabelText(label) as HTMLSelectElement).options
      ).map((option) => option.textContent)

    expect(options("Number of stations")).toEqual(["2", "3", "4", "5"])
    expect(options("Target response time")).toEqual(["3 min", "4 min", "5 min", "8 min"])
  })

  it("shows a loading message, then the chosen stations as numbered markers", async () => {
    const answer = deferred<Reply>()
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/station-set": () => answer.promise,
    })
    const user = userEvent.setup()
    render(<App />)
    await user.selectOptions(screen.getByLabelText("Number of stations"), "4")

    await user.click(setButton())

    expect(screen.getByText(/Choosing 4 stations that work well together/)).toBeTruthy()
    expect(setButton().disabled).toBe(true)
    expect(rankingButton().disabled).toBe(true)
    expect(markers(CHOSEN_STATION)).toHaveLength(0)

    await act(async () => {
      answer.resolve({ body: stationSetAnswer(4) })
    })

    expect(await screen.findByText(/Recommended: 4 stations/)).toBeTruthy()
    expect(screen.queryByText(/Choosing 4 stations/)).toBeNull()
    expect(markerNumbers(CHOSEN_STATION)).toEqual(["1", "2", "3", "4"])
    expect(screen.getByText("Best single station")).toBeTruthy()
  })
})

describe("clearing results", () => {
  it("has no Clear button until there are results", () => {
    mockBackend(workingBackend())
    render(<App />)

    expect(clearButton()).toBeNull()
  })

  it("removes the ranking table and its markers", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/stations": { body: rankingAnswer(3) },
    })
    const user = userEvent.setup()
    render(<App />)
    await user.click(rankingButton())
    await screen.findByText(/Recommended: candidate #1/)
    expect(markers(RANKED_STATION)).toHaveLength(3)

    await user.click(clearButton()!)

    expect(screen.queryByText(/Recommended: candidate #1/)).toBeNull()
    expect(markers(RANKED_STATION)).toHaveLength(0)
    expect(clearButton()).toBeNull()
  })

  it("removes both a ranking and a chosen set at once", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/stations": { body: rankingAnswer(3) },
      "GET /optimiser/station-set": { body: stationSetAnswer(4) },
    })
    const user = userEvent.setup()
    render(<App />)
    await user.click(rankingButton())
    await screen.findByText(/Recommended: candidate #1/)
    await user.click(setButton())
    await screen.findByText(/Recommended: 4 stations/)
    expect(markers(RANKED_STATION)).toHaveLength(3)
    expect(markers(CHOSEN_STATION)).toHaveLength(4)

    await user.click(clearButton()!)

    expect(screen.queryByText(/Recommended:/)).toBeNull()
    expect(markers(RANKED_STATION)).toHaveLength(0)
    expect(markers(CHOSEN_STATION)).toHaveLength(0)
  })
})

describe("when the optimiser fails", () => {
  it("shows the backend's explanation and leaves the buttons usable", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/station-set": {
        status: 400,
        body: { detail: "Stations must be between 2 and 20" },
      },
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(setButton())

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toBe(
      "Optimiser failed: Stations must be between 2 and 20"
    )
    expect(setButton().disabled).toBe(false)
    expect(rankingButton().disabled).toBe(false)
    expect(markers(CHOSEN_STATION)).toHaveLength(0)
    expect(clearButton()).toBeNull()
  })

  it("shows a message when the backend cannot be reached", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/stations": new TypeError("Failed to fetch"),
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(rankingButton())

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Optimiser failed: Failed to fetch"
    )
    expect(rankingButton().disabled).toBe(false)
  })

  it("shows a plain message when the backend answers with something unreadable", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/stations": { status: 500, unreadable: true },
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(rankingButton())

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Optimiser failed: The optimiser failed"
    )
  })

  it("does not show the technical detail of a validation error", async () => {
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/stations": {
        status: 422,
        body: { detail: [{ loc: ["query", "scenarios"], msg: "bad" }] },
      },
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(rankingButton())

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Optimiser failed: The optimiser failed"
    )
  })

  it("removes the error when the next attempt works", async () => {
    let attempt = 0
    mockBackend({
      ...workingBackend(),
      "GET /optimiser/stations": () => {
        attempt += 1
        return attempt === 1
          ? { status: 500, body: { detail: "Try again later" } }
          : { body: rankingAnswer(2) }
      },
    })
    const user = userEvent.setup()
    render(<App />)

    await user.click(rankingButton())
    await screen.findByRole("alert")

    await user.click(rankingButton())

    expect(await screen.findByText(/Recommended: candidate #1/)).toBeTruthy()
    expect(screen.queryByRole("alert")).toBeNull()
  })
})
