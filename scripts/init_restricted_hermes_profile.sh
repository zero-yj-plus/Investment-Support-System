#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=client_runtime.sh
source "$ROOT/scripts/client_runtime.sh"
hermes_resolve_client_context "$ROOT" "${1:-}"

TEMPLATE="$CLIENT_DIR/hermes-profile.example"
PROFILE="$CLIENT_DIR/data/hermes-profile"

mkdir -p "$PROFILE"
expected="$(cd "$CLIENT_DIR/data" && pwd -P)/hermes-profile"
actual="$(cd "$PROFILE" && pwd -P)"
if [[ "$actual" != "$expected" ]]; then
  echo "Refusing profile path escape or symlink: $actual" >&2
  exit 1
fi
chmod 700 "$PROFILE"
cp "$TEMPLATE/config.yaml" "$PROFILE/config.yaml"
key=""
if [[ -f "$PROFILE/.env" ]]; then
  key="$(sed -n 's/^API_SERVER_KEY=//p' "$PROFILE/.env" | head -1)"
fi
if [[ ! "$key" =~ ^[0-9a-fA-F]{64}$ ]]; then
  key="$(openssl rand -hex 32)"
fi
sed "s/GENERATE_WITH_OPENSSL_RAND_HEX_32/$key/" "$TEMPLATE/.env.example" > "$PROFILE/.env"
chmod 600 "$PROFILE/config.yaml" "$PROFILE/.env"

echo "Restricted Hermes profile initialized: $PROFILE"
echo "Next: HERMES_HOME=\"$PROFILE\" hermes auth add openai-codex"
echo "Then: $ROOT/scripts/run_restricted_hermes.sh $CLIENT_ID"
