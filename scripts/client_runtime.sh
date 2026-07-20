#!/usr/bin/env bash
# Shared client-path resolver for the project-scoped Hermes runtime scripts.
#
# Callers may pass the client ID as the first argument or set
# HERMES_CLIENT_ID.  A packaged release contains exactly one client and may
# therefore omit both; a multi-client development checkout must be explicit.

hermes_resolve_client_context() {
  local root="${1:?project root is required}"
  local requested="${2:-${HERMES_CLIENT_ID:-}}"
  local candidates=()
  local candidate=""

  if [[ -z "$requested" ]]; then
    for candidate in "$root"/clients/*; do
      [[ -d "$candidate/hermes-profile.example" ]] || continue
      candidates+=("${candidate##*/}")
    done
    if [[ "${#candidates[@]}" -ne 1 ]]; then
      echo "Client ID is required in a multi-client checkout: pass it as the first argument or set HERMES_CLIENT_ID." >&2
      return 1
    fi
    requested="${candidates[0]}"
  fi

  if [[ ! "$requested" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]; then
    echo "Invalid client ID: $requested" >&2
    return 1
  fi

  CLIENT_ID="$requested"
  CLIENT_DIR="$root/clients/$CLIENT_ID"
  if [[ ! -d "$CLIENT_DIR" || -L "$CLIENT_DIR" ]]; then
    echo "Client directory is missing or unsafe: clients/$CLIENT_ID" >&2
    return 1
  fi
  if [[ ! -f "$CLIENT_DIR/hermes-profile.example/config.yaml" || \
        ! -f "$CLIENT_DIR/hermes-profile.example/.env.example" ]]; then
    echo "Restricted Hermes template is incomplete: clients/$CLIENT_ID/hermes-profile.example" >&2
    return 1
  fi

  HERMES_CLIENT_ID="$CLIENT_ID"
  export HERMES_CLIENT_ID
}
