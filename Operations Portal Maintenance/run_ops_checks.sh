#!/usr/bin/env bash
# One-shot operations run: health, then configuration deviations, then a dated report.
# Read-only. Requires LM_ACCOUNT, LM_ACCESS_ID, LM_ACCESS_KEY (or a repo-root .env).

set -euo pipefail
cd "$(dirname "$0")"

python3 health_check.py "$@"
python3 config_deviation.py "$@"
python3 maintenance_report.py "$@"
