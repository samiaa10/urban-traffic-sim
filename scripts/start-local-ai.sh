#!/usr/bin/env bash
# Start the Norwich Urban Traffic Simulator's backend with the local AI analyst (Ollama, free, on
# this machine only). See docs/AI.md.
#
#   scripts/start-local-ai.sh                 # the recommended model (llama3.2:3b)
#   AI_MODEL=qwen2.5:3b scripts/start-local-ai.sh
#
# Then, in another terminal: cd frontend && npm run dev, and open the site.
# Ollama listens on 127.0.0.1 only: nothing is reachable from other machines.

set -euo pipefail

MODEL="${AI_MODEL:-llama3.2:3b}"
HOST="127.0.0.1:11434"

OLLAMA="$(command -v ollama || true)"
for candidate in "$HOME/Applications/Ollama.app/Contents/Resources/ollama" "/Applications/Ollama.app/Contents/Resources/ollama"; do
  if [ -z "$OLLAMA" ] && [ -x "$candidate" ]; then OLLAMA="$candidate"; fi
done
if [ -z "$OLLAMA" ]; then
  echo "Ollama is not installed: get it from https://ollama.com, then run this again." >&2
  exit 1
fi

if ! curl -s --max-time 2 "http://$HOST/api/version" >/dev/null; then
  echo "Starting Ollama on $HOST (this machine only)..."
  OLLAMA_HOST="$HOST" OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 "$OLLAMA" serve >/tmp/norwich-sim-ollama.log 2>&1 &
  for _ in $(seq 1 30); do
    curl -s --max-time 1 "http://$HOST/api/version" >/dev/null && break
    sleep 1
  done
fi

if ! OLLAMA_HOST="$HOST" "$OLLAMA" list | awk '{print $1}' | grep -qx "$MODEL"; then
  echo "Downloading $MODEL (once)..."
  OLLAMA_HOST="$HOST" "$OLLAMA" pull "$MODEL"
fi

# Load the model now, so the first question does not wait for it
curl -s --max-time 120 "http://$HOST/api/generate" -d "{\"model\": \"$MODEL\", \"keep_alive\": \"15m\"}" >/dev/null || true

cd "$(dirname "$0")/.."
PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

echo "Backend on http://127.0.0.1:8000 with the local AI analyst ($MODEL)."
exec env AI_PROVIDER=ollama AI_MODEL="$MODEL" OLLAMA_BASE_URL="http://$HOST" \
  "$PYTHON" -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
