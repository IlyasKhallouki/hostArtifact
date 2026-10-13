#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/lib.sh"
exec uv run --project "$REPO_ROOT/server" python "$REPO_ROOT/scripts/cloud_infra.py" bootstrap "$@"
