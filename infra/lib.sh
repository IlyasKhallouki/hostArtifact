#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$REPO_ROOT/infra/config.env" ]]; then
  echo 'Copy infra/config.env.example to infra/config.env and fill in your deployment settings.' >&2
  exit 2
fi
set -a
source "$REPO_ROOT/infra/config.env"
set +a
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
admin() { uv run --project "$REPO_ROOT/server" publisher-admin "$@"; }
