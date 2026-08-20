#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON=("$ROOT/.venv/bin/python")
elif command -v uv >/dev/null 2>&1; then
  PYTHON=(uv run --with-requirements "$ROOT/requirements.txt" python)
else
  echo "缺少 Python 运行时：请创建 .venv 或安装 uv。" >&2
  exit 1
fi

"${PYTHON[@]}" -m hermes_trader.worker --config "$ROOT/config.yaml" &
WUDI_WORKER_PID=$!

cleanup_worker() {
  if kill -0 "$WUDI_WORKER_PID" 2>/dev/null; then
    kill "$WUDI_WORKER_PID" 2>/dev/null || true
    wait "$WUDI_WORKER_PID" 2>/dev/null || true
  fi
}
trap cleanup_worker EXIT INT TERM

"${PYTHON[@]}" -m hermes_trader --config "$ROOT/config.yaml"
