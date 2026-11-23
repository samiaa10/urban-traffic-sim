// Scenarios from ordinary language: the visitor describes one, the backend
// proposes supported actions in real Norwich places (nothing runs yet),
// the visitor checks and confirms, and a new live run starts with them.

import { useState } from "react"

import {
  SCENARIO_EXAMPLES,
  planScenario,
  runScenario,
  type Proposal,
  type ScenarioResult,
} from "./intelligence.ts"

export function ScenarioBuilder({ onOpenRun }: { onOpenRun: (simulationId: string) => void }) {
  const [text, setText] = useState("")
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [result, setResult] = useState<ScenarioResult | null>(null)
  const [busy, setBusy] = useState<"plan" | "run" | null>(null)
  const [error, setError] = useState("")

  async function propose(request: string) {
    const value = request.trim()
    if (!value || busy) return
    setText(value)
    setBusy("plan")
    setError("")
    setProposal(null)
    setResult(null)
    try {
      setProposal(await planScenario(value))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  async function confirm() {
    if (!proposal || busy) return
    setBusy("run")
    setError("")
    try {
      setResult(await runScenario(proposal.proposal))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="scenario-builder">
      <form onSubmit={(e) => { e.preventDefault(); void propose(text) }}>
        <label htmlFor="scenario-text">Describe a scenario</label>
        <textarea id="scenario-text" rows={3} maxLength={600} value={text}
          placeholder="e.g. Close Dereham Road during an evening rush hour"
          onChange={(e) => setText(e.target.value)} />
        <button type="submit" disabled={!text.trim() || busy !== null}>
          {busy === "plan" ? "Reading…" : "Propose a scenario"}
        </button>
      </form>
      {!proposal && !result && (
        <div className="analyst-examples">
          {SCENARIO_EXAMPLES.map((example) => (
            <button key={example} type="button" onClick={() => void propose(example)}>{example}</button>
          ))}
        </div>
      )}
      {error && <p className="analyst-error" role="alert">{error}</p>}

      {proposal && !result && (
        <section className="scenario-proposal" aria-label="Proposed scenario">
          <span className={`analyst-badge analyst-badge--${proposal.mode === "ai" ? "ai" : "calc"}`}>
            {proposal.mode === "ai" ? `Proposed by ${proposal.model}` : "Proposed by the built-in rules"}
          </span>
          <strong>{proposal.proposal.title}</strong>
          <ol>
            {proposal.summary.map((line) => <li key={line}>{line}</li>)}
          </ol>
          {proposal.explanation && <p className="scenario-intent"><b>AI-generated:</b> {proposal.explanation}</p>}
          {proposal.proposal.notes && <p className="analyst-notice">{proposal.proposal.notes}</p>}
          {proposal.notice && <p className="analyst-notice">{proposal.notice}</p>}
          <p className="scenario-check">Nothing has run yet. Check the actions, then run them on the simulation engine.</p>
          <div className="scenario-actions">
            <button type="button" className="scenario-run" onClick={() => void confirm()} disabled={busy !== null}>
              {busy === "run" ? "Starting…" : "Run this scenario"}
            </button>
            <button type="button" onClick={() => setProposal(null)} disabled={busy !== null}>Discard</button>
          </div>
        </section>
      )}

      {result && (
        <section className="scenario-proposal" aria-label="Scenario running">
          <span className="analyst-badge analyst-badge--calc">Running on the simulation engine</span>
          <strong>{result.title}</strong>
          <ul>
            {result.applied.map((line) => <li key={line}>{line}</li>)}
          </ul>
          {result.impact && (
            <p className="scenario-impact">
              <b>Calculated:</b> the {result.impact.roads_closed} closed road segment(s) make{" "}
              {result.impact.trips_longer_percent}% of sampled trips longer, by {Math.round(result.impact.mean_extra_seconds)} s
              on average{result.impact.trips_cut_off ? `; ${result.impact.trips_cut_off} trips have no route` : ""}.
              <small>{result.impact.method}.</small>
            </p>
          )}
          <div className="scenario-actions">
            <button type="button" className="scenario-run" onClick={() => onOpenRun(result.simulation_id)}>
              Watch it in the command centre
            </button>
            <button type="button" onClick={() => { setResult(null); setProposal(null); setText("") }}>New scenario</button>
          </div>
        </section>
      )}
    </div>
  )
}
