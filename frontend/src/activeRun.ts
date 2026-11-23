// The live simulation the visitor is watching (the route simulation's run
// or the command centre's), so the analyst can ask about it and the
// scenario builder can open what it starts.

let current: string | null = null
const listeners = new Set<(id: string | null) => void>()

export function activeRun(): string | null {
  return current
}

export function setActiveRun(id: string | null) {
  current = id
  for (const listener of listeners) listener(id)
}

export function onActiveRun(listener: (id: string | null) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
