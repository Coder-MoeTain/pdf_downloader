#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi
exec python main.py sync-lms "$@"
