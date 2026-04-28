#!/usr/bin/env bash
# One-time setup for native (no-Docker) local dev on macOS.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)

echo "==> Checking Python 3.11..."
if command -v python3.11 >/dev/null 2>&1; then
  PY=python3.11
elif command -v python3.12 >/dev/null 2>&1; then
  PY=python3.12
else
  echo "Python 3.11+ not found. Install via:"
  echo "    brew install python@3.11"
  exit 1
fi
echo "    using $($PY --version) at $(which $PY)"

echo "==> Checking pnpm..."
if ! command -v pnpm >/dev/null 2>&1; then
  echo "pnpm not found. Install via:  brew install pnpm"
  exit 1
fi
echo "    pnpm $(pnpm --version)"

echo "==> Copying .env.example -> .env (if missing)..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    created .env — remember to fill MINIMAX_PLAN_API_KEY"
else
  echo "    .env already exists, leaving as-is"
fi

echo "==> Creating Python venv + installing API deps..."
cd "$ROOT/apps/api"
if [ ! -d .venv ]; then
  $PY -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt
deactivate

echo "==> Installing web deps..."
cd "$ROOT/apps/web"
pnpm install

echo "==> Preparing data dirs..."
cd "$ROOT"
mkdir -p data/uploads

echo ""
echo "Setup complete!"
echo ""
echo "Next:"
echo "  1. Edit .env and set MINIMAX_PLAN_API_KEY"
echo "  2. Start API:  ./scripts/dev-api.sh"
echo "  3. Start Web:  ./scripts/dev-web.sh   (in another terminal)"
echo "  4. Open http://localhost:3000"
