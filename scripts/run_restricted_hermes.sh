#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=client_runtime.sh
source "$ROOT/scripts/client_runtime.sh"
hermes_resolve_client_context "$ROOT" "${1:-}"

PROFILE="$CLIENT_DIR/data/hermes-profile"
if [[ ! -f "$PROFILE/config.yaml" || ! -f "$PROFILE/.env" ]]; then
  echo "Restricted profile missing. Run scripts/init_restricted_hermes_profile.sh $CLIENT_ID first." >&2
  exit 1
fi
expected="$(cd "$CLIENT_DIR/data" && pwd -P)/hermes-profile"
actual="$(cd "$PROFILE" && pwd -P)"
if [[ "$actual" != "$expected" ]]; then
  echo "Refusing profile path escape or symlink: $actual" >&2
  exit 1
fi
if ! cmp -s "$PROFILE/config.yaml" "$CLIENT_DIR/hermes-profile.example/config.yaml"; then
  echo "Restricted profile config drifted. Re-run scripts/init_restricted_hermes_profile.sh $CLIENT_ID." >&2
  exit 1
fi

# Executing a file under scripts/ puts only scripts/ on sys.path.  Keep the
# product root importable so the restricted proxy can reuse product-owned,
# security-reviewed bridge modules without installing the package globally.
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/restricted_hermes_proxy.py"
fi
if command -v uv >/dev/null 2>&1; then
  exec uv run --with-requirements "$ROOT/requirements.txt" python "$ROOT/scripts/restricted_hermes_proxy.py"
fi
echo "No project Python runtime found (.venv or uv required)." >&2
exit 1
