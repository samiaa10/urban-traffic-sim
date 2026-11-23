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
  TOOL_LABELS,
  ask,
  describeInput,
  provenanceOf,
  type Answer,
  type Status,
} from "./intelligence.ts"
import { ScenarioBuilder } from "./ScenarioBuilder.tsx"

type Turn = { question: string; answer: Answer | null; error: string | null }

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
    setTurns((list) => [...list, { question: q, answer: null, error: null }])
    try {
      const answer = await ask(q, activeRun(), history)
      setTurns((list) => list.map((t, i) => (i === list.length - 1 ? { ...t, answer } : t)))
    } catch (error) {
      setTurns((list) => list.map((t, i) => (i === list.length - 1 ? { ...t, error: error instanceof Error ? error.message : String(error) } : t)))
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
          <span className={status?.available ? "analyst-mode analyst-mode--ai" : "analyst-mode"}>
            {status === null ? "Connecting…" : status.available ? `AI: ${status.model}` : "Built-in rules (no AI model configured)"}
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
                  <AnswerCard answer={turn.answer} />
                ) : (
                  <p className="analyst-working" role="status">Calculating…</p>
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

function AnswerCard({ answer }: { answer: Answer }) {
  const provenance = provenanceOf(answer)
  return (
    <div className="analyst-answer">
      <span className={`analyst-badge analyst-badge--${provenance.tone}`} title={provenance.detail}>{provenance.label}</span>
      <div className="analyst-text">
        {answer.answer.split("\n").map((line, i) => (line.startsWith("- ")
          ? <p key={i} className="analyst-bullet">{line.slice(2)}</p>
          : <p key={i}>{line}</p>))}
      </div>
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
