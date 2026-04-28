#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../apps/web"

if [ ! -d node_modules ]; then
  echo "node_modules not found. Run ./scripts/setup.sh first."
  exit 1
fi

export NEXT_PUBLIC_API_BASE="${NEXT_PUBLIC_API_BASE:-http://localhost:8000}"
exec pnpm dev
