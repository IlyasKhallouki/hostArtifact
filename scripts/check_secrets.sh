#!/usr/bin/env bash
set -euo pipefail
if command -v gitleaks >/dev/null; then gitleaks protect --staged; exit; fi
python3 scripts/scan_secrets.py
