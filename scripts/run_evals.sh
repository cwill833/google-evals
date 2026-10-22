#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export CI=1 AGENTS_CLI_TELEMETRY=0
if [[ ! -x .venv/bin/python ]]; then
  echo 'Missing environment. Run: uv sync --locked --python 3.12' >&2
  exit 2
fi
exec .venv/bin/python scripts/run_evals.py "$@"
