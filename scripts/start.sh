#!/usr/bin/env bash
# One-command boot for Helmsman.
# - Installs Coral CLI if missing
# - Installs Helmsman's demo Coral sources (idempotent)
# - Starts the FastAPI backend (port 8787) and Next.js frontend (port 3000)
#
# Usage:
#   ./scripts/start.sh              # boot everything
#   HELMSMAN_PORT=8788 ./scripts/start.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ---------------- Coral CLI ----------------
if ! command -v coral >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/coral" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "==> Coral not installed. Installing..."
    curl -fsSL https://withcoral.com/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi
echo "==> coral: $(coral --version)"

# ---------------- Demo sources ----------------
need_install=0
for src in github_demo linear_demo slack_demo sentry_demo datadog_demo calendar_demo; do
  if ! coral source list 2>/dev/null | awk 'NR>2{print $1}' | grep -qx "$src"; then
    need_install=1
    break
  fi
done
if [ "$need_install" -eq 1 ]; then
  echo "==> Installing Helmsman's demo Coral sources..."
  bash scripts/install_demo_sources.sh >/dev/null
  echo "    done."
else
  echo "==> Demo sources already installed."
fi

# ---------------- Python venv ----------------
if [ ! -d ".venv" ]; then
  echo "==> Creating Python venv..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
# Re-install whenever any module from requirements.txt is missing. This makes
# the script idempotent across dependency bumps (e.g. adding bcrypt).
need_pip=0
for mod in fastapi bcrypt cryptography itsdangerous email_validator; do
  if ! python -c "import $mod" >/dev/null 2>&1; then
    need_pip=1
    break
  fi
done
if [ "$need_pip" -eq 1 ]; then
  echo "==> Installing backend deps..."
  pip install -q --upgrade pip
  pip install -q -r backend/requirements.txt
fi

# ---------------- Frontend deps ----------------
if [ ! -d "frontend/node_modules" ]; then
  echo "==> Installing frontend deps..."
  (cd frontend && npm install --no-audit --no-fund --loglevel=error)
fi

# ---------------- Boot ----------------
PORT="${HELMSMAN_PORT:-8787}"
echo "==> Booting Helmsman backend on http://127.0.0.1:${PORT}"
( cd backend && uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level info ) &
BACKEND_PID=$!

echo "==> Booting Helmsman frontend on http://127.0.0.1:3000"
( cd frontend && HELMSMAN_BACKEND="http://127.0.0.1:${PORT}" npm run dev ) &
FRONTEND_PID=$!

cleanup() {
  echo
  echo "==> Stopping Helmsman..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cat <<EOF

  ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
  ┃                                                          ┃
  ┃   Helmsman is at the helm.                               ┃
  ┃                                                          ┃
  ┃   ⛵  Open  http://localhost:3000  to set sail.          ┃
  ┃                                                          ┃
  ┃   API:    http://localhost:${PORT}                          ┃
  ┃   Docs:   http://localhost:${PORT}/docs                     ┃
  ┃                                                          ┃
  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

  (Ctrl-C to stop both servers.)
EOF

wait "$BACKEND_PID" "$FRONTEND_PID"
