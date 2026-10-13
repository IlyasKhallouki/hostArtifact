#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
uv run --project "$REPO_ROOT/server" python "$REPO_ROOT/scripts/cloud_infra.py" deploy "$@"
if [[ " ${*} " == *" --apply "* && " ${*} " != *" --dry-run "* ]]; then
  "$REPO_ROOT/infra/smoke_test.sh" "$@" >&2
fi
