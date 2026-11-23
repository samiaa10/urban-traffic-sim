// The AI City Analyst: a drawer where visitors ask about Norwich and the
// live simulation. Every answer says who wrote it (the AI model or the
// built-in rules) and lists the calculations it came from, inputs and
// results, so nothing has to be taken on trust.

import { useEffect, useRef, useState } from "react"

import { activeRun } from "./activeRun.ts"
import { API_URL } from "./config.ts"
import { Icon } from "./components.tsx"
import {
  EXAMPLES,
  STAGE_TEXT,
  TOOL_LABELS,
  askStream,
  describeInput,
  modeLabel,
  provenanceOf,
  runScenario,
  type ScenarioResult,
  type Answer,
  type Stage,
  type Status,
} from "./intelligence.ts"
import { ScenarioBuilder } from "./ScenarioBuilder.tsx"

type Turn = {
  question: string
  answer: Answer | null
  error: string | null
  // While a local model works: the stages it has reached, and the
  // calculated facts once they exist
  stages?: Stage[]
  partial?: Answer | null
  started?: number
}

// Live progress for one question: what has been done, what is happening
// now, and the seconds so far, so a slow local model never looks frozen
function Progress({ turn, local, model }: { turn: Turn; local: boolean; model: string | null }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  const seconds = Math.max(0, Math.round((now - (turn.started ?? now)) / 1000))
  if (!local) {
    return <p className="analyst-working" role="status">Calculating…{seconds > 2 ? ` ${seconds} s` : ""}</p>
  }
  const reached = turn.stages ?? []
  const current = reached[reached.length - 1]
  const steps: Exclude<Stage, "done">[] = reached.includes("proposing")
    ? ["planning", "calculated", "proposing", "explaining"]
    : ["planning", "calculated", "explaining"]
  const label = (step: Exclude<Stage, "done">) => (step === "calculated"
    ? (reached.includes("calculated") ? "Ran the simulation's calculations" : "Run the simulation's calculations")
    : STAGE_TEXT[step])
  // "calculated" is instant: it is done as soon as it is reached
  const state = (step: Exclude<Stage, "done">) => (!reached.includes(step) ? ""
    : step === current && step !== "calculated" ? "is-active" : "is-done")
  return (
    <div className="analyst-progress" role="status" aria-label="Progress">
      <ol>{steps.map((step) => <li key={step} className={state(step)}>{label(step)}</li>)}</ol>
      <small>{model ? `${model}, running on this machine` : "Local model"} · {seconds} s</small>
    </div>
  )
}

function Lines({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").filter((line) => line.trim()).map((line, i) => (line.startsWith("- ")
        ? <p key={i} className="analyst-bullet">{line.slice(2)}</p>
        : <p key={i}>{line}</p>))}
    </>
  )
}

// A scenario the analyst proposed from the conversation: nothing runs
// until the user confirms
function ProposalCard({ proposal, onOpenRun }: { proposal: NonNullable<Answer["proposal"]>; onOpenRun: (id: string) => void }) {
  const [result, setResult] = useState<ScenarioResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  async function confirm() {
    setBusy(true)
    setError("")
    try {
      setResult(await runScenario(proposal.proposal))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="scenario-proposal" aria-label="Proposed scenario">
      <span className={`analyst-badge analyst-badge--${proposal.mode === "ai" ? "ai" : "calc"}`}>
        {proposal.mode === "ai" ? `Scenario proposed by ${proposal.model}` : "Scenario proposed by the built-in rules"}
      </span>
      <strong>{proposal.proposal.title}</strong>
      <ol>{proposal.summary.map((line) => <li key={line}>{line}</li>)}</ol>
      {proposal.explanation && <p className="scenario-intent"><b>AI-generated:</b> {proposal.explanation}</p>}
      {proposal.notice && <p className="analyst-notice">{proposal.notice}</p>}
      {error && <p className="analyst-error" role="alert">{error}</p>}
      {result ? (
        <div className="scenario-actions">
          <button type="button" className="scenario-run" onClick={() => onOpenRun(result.simulation_id)}>Watch it in the command centre</button>
        </div>
      ) : (
        <>
          <p className="scenario-check">Nothing has run yet. Check the actions above, then run them on the simulation engine.</p>
          <div className="scenario-actions">
            <button type="button" className="scenario-run" disabled={busy} onClick={() => void confirm()}>
              {busy ? "Starting…" : "Run this scenario"}
            </button>
          </div>
        </>
      )}
    </section>
  )
}

export function AnalystDrawer({ open, onClose, onOpenRun }: {
  open: boolean
  onClose: () => void
  onOpenRun: (simulationId: string) => void
}) {
  const [tab, setTab] = useState<"ask" | "scenario">("ask")
  const [status, setStatus] = useState<Status | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState("")
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open || status) return
    fetch(`${API_URL}/intelligence/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => { if (body) setStatus(body) })
      .catch(() => {})
  }, [open, status])

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end", behavior: "smooth" })
  }, [turns])

  async function submit(text: string) {
    const q = text.trim()
    if (!q || busy) return
    setBusy(true)
    setQuestion("")
    const history = turns.flatMap((t) => t.answer
      ? [{ role: "user" as const, content: t.question }, { role: "assistant" as const, content: t.answer.answer }]
      : [])
    setTurns((list) => [...list, { question: q, answer: null, error: null, stages: [], partial: null, started: Date.now() }])
    const update = (change: (turn: Turn) => Turn) =>
      setTurns((list) => list.map((t, i) => (i === list.length - 1 ? change(t) : t)))
    try {
      const answer = await askStream(q, activeRun(), history, (event) => {
        if (event.stage === "done") return
        update((t) => ({
          ...t,
          stages: [...(t.stages ?? []), event.stage],
          partial: event.stage === "calculated" && event.answer ? event.answer : t.partial,
        }))
      })
      update((t) => ({ ...t, answer }))
    } catch (error) {
      update((t) => ({ ...t, error: error instanceof Error ? error.message : String(error) }))
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  return (
    <aside className="analyst" role="dialog" aria-modal="false" aria-label="City Analyst"
      onKeyDown={(e) => { if (e.key === "Escape") onClose() }}>
      <header className="analyst-head">
        <div>
          <strong>City Analyst</strong>
          <span className={modeLabel(status).ai ? "analyst-mode analyst-mode--ai" : "analyst-mode"}>
            {modeLabel(status).text}
          </span>
        </div>
        <button type="button" aria-label="Close the City Analyst" onClick={onClose}><Icon name="close" size={16} /></button>
      </header>

      <div className="analyst-tabs" role="tablist" aria-label="Analyst">
        <button type="button" role="tab" aria-selected={tab === "ask"} onClick={() => setTab("ask")}>Ask</button>
        <button type="button" role="tab" aria-selected={tab === "scenario"} onClick={() => setTab("scenario")}>Create a scenario</button>
      </div>

      {tab === "scenario" ? (
        <ScenarioBuilder onOpenRun={onOpenRun} />
      ) : (
        <>
          <div className="analyst-thread" aria-live="polite">
            {turns.length === 0 && (
              <div className="analyst-intro">
                <p>Ask about emergency coverage, bottlenecks, closures, station locations, a live simulation or saved
                  scenario studies. Answers come from the simulation's own calculations, listed under each answer.</p>
                <div className="analyst-examples">
                  {EXAMPLES.map((example) => (
                    <button key={example} type="button" onClick={() => void submit(example)}>{example}</button>
                  ))}
                </div>
              </div>
            )}
            {turns.map((turn, index) => (
              <div key={index} className="analyst-turn">
                <p className="analyst-question">{turn.question}</p>
                {turn.error ? (
                  <p className="analyst-error" role="alert">{turn.error}</p>
                ) : turn.answer ? (
                  <AnswerCard answer={turn.answer} onOpenRun={onOpenRun} />
                ) : (
                  <>
                    {turn.partial && <AnswerCard answer={turn.partial} onOpenRun={onOpenRun} pending />}
                    <Progress turn={turn} local={Boolean(status?.available && status.local)} model={status?.model ?? null} />
                  </>
                )}
              </div>
            ))}
            <div ref={end} />
          </div>

          <form className="analyst-ask" onSubmit={(e) => { e.preventDefault(); void submit(question) }}>
            <label className="sr-only" htmlFor="analyst-question">Your question</label>
            <textarea id="analyst-question" rows={2} maxLength={1000} value={question} placeholder="Ask about Norwich…"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void submit(question) } }} />
            <button type="submit" disabled={busy || !question.trim()}>Ask</button>
          </form>
        </>
      )}
    </aside>
  )
}

function AnswerCard({ answer, onOpenRun, pending = false }: {
  answer: Answer
  onOpenRun: (simulationId: string) => void
  pending?: boolean
}) {
  const provenance = provenanceOf(answer)
  const facts = answer.facts ?? []
  const separate = answer.mode === "ai" && facts.length > 0
  return (
    <div className="analyst-answer">
      <span className={`analyst-badge analyst-badge--${provenance.tone}`} title={provenance.detail}>{provenance.label}</span>
      {separate ? (
        <>
          <section className="analyst-facts" aria-label="Calculated facts">
            <h4><span className="analyst-badge analyst-badge--calc">Calculated</span> Facts from the simulation</h4>
            {facts.map((fact, i) => (
              <div key={i} className="analyst-fact">
                <strong>{TOOL_LABELS[fact.tool] ?? fact.tool}</strong>
                <Lines text={fact.text} />
              </div>
            ))}
          </section>
          <section className="analyst-explanation" aria-label="AI explanation">
            <h4><span className="analyst-badge analyst-badge--ai">AI-generated</span> Explanation by {answer.model}</h4>
            {pending
              ? <p className="analyst-pending">{answer.model} is explaining these facts…</p>
              : answer.explanation
                ? <><Lines text={answer.explanation} />
                  {answer.explanation_note && <p className="analyst-notice">{answer.explanation_note}</p>}</>
                : <p className="analyst-notice">{answer.explanation_note ?? "No explanation."}</p>}
          </section>
        </>
      ) : (
        <div className="analyst-text"><Lines text={answer.answer} /></div>
      )}
      {answer.proposal && <ProposalCard proposal={answer.proposal} onOpenRun={onOpenRun} />}
      {answer.notice && <p className="analyst-notice">{answer.notice}</p>}
      {answer.sources.length > 0 && (
        <details className="analyst-sources">
          <summary>{answer.sources.length} calculation{answer.sources.length === 1 ? "" : "s"} used</summary>
          {answer.sources.map((source, i) => (
            <div key={i} className="analyst-source">
              <strong>{TOOL_LABELS[source.tool] ?? source.tool}</strong>
              {describeInput(source.input) && <span>{describeInput(source.input)}</span>}
              {typeof source.result.method === "string" && <small>{source.result.method}</small>}
              <details>
                <summary>Result data</summary>
                <pre>{JSON.stringify(source.result, null, 2).slice(0, 6000)}</pre>
              </details>
            </div>
          ))}
        </details>
      )}
    </div>
  )
}
