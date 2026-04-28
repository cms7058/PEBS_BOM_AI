#!/usr/bin/env bash
# .env is read by Pydantic (not sourced by shell) so special chars in keys are safe.
set -euo pipefail
cd "$(dirname "$0")/../apps/api"

if [ ! -d .venv ]; then
  echo "venv not found. Run ./scripts/setup.sh first."
  exit 1
fi

source .venv/bin/activate
exec uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
