#!/usr/bin/env bash
# Starts an Ollama server inside a Valohai execution and keeps it running.
# Usage: bash server/start_server.sh <model-tag>
set -euo pipefail

MODEL="${1:-qwen2.5:1.5b}"
MODEL="${MODEL#--model=}"   # tolerate Valohai passing "--model=<tag>" instead of the bare value
PORT=11434

echo "==> Starting Ollama server on 0.0.0.0:${PORT}"
# Bind to all interfaces so Docker's port mapping (VH_EXPOSE_PORTS) can reach it.
OLLAMA_HOST="0.0.0.0:${PORT}" ollama serve &
SERVER_PID=$!

# The CLI talks to the local server
export OLLAMA_HOST="127.0.0.1:${PORT}"

echo "==> Waiting for server to come up"
for i in $(seq 1 60); do
  if ollama list >/dev/null 2>&1; then break; fi
  sleep 1
done

echo "==> Pulling model ${MODEL} (first run downloads it, can take a minute or two)"
# Progress spinner floods the Valohai log, so only keep the last lines (pipefail still catches errors)
ollama pull "${MODEL}" 2>&1 | tail -n 2

echo "==> Warming up the model"
# </dev/null is required: with no TTY, `ollama run` waits for stdin to close and hangs forever.
timeout 120 ollama run "${MODEL}" "Reply with the single word: ready" </dev/null || echo "(warm-up skipped)"

# Fallback: ask the EC2 metadata service (no curl in this image, so use bash's /dev/tcp)
imds() {
  local v
  v=$(timeout 3 bash -c 'exec 3<>/dev/tcp/169.254.169.254/80 && printf "GET /latest/meta-data/%s HTTP/1.0\r\nHost: 169.254.169.254\r\n\r\n" "$1" >&3 && tail -n 1 <&3' _ "$1" 2>/dev/null) || true
  [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && echo "$v" || echo "unknown"
}
PRIVATE_IP="${VH_LOCAL_IP:-$(imds local-ipv4)}"
PUBLIC_IP="${VH_PUBLIC_IP:-$(imds public-ipv4)}"

echo ""
echo "============================================================"
echo " LLM endpoint is READY"
echo "   Model:              ${MODEL}"
echo "   From other workers: http://${PRIVATE_IP}:${PORT}"
echo "   Public IP (only if SG allows your IP): http://${PUBLIC_IP}:${PORT}"
echo "   OpenAI-compatible:  http://${PRIVATE_IP}:${PORT}/v1/chat/completions"
echo "============================================================"
echo ""

# Valohai picks up JSON lines on stdout as execution metadata -> endpoint visible in the UI
echo "{\"llm_endpoint\": \"http://${PRIVATE_IP}:${PORT}\", \"llm_public_endpoint\": \"http://${PUBLIC_IP}:${PORT}\", \"model\": \"${MODEL}\"}"

# Keep the execution alive as long as the server runs. Stop the execution in the UI when done.
wait "${SERVER_PID}"
