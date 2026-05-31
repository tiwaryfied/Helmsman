#!/usr/bin/env bash
# Install Helmsman's demo Coral sources.
# 1) seed JSONL data 2) generate source YAMLs 3) lint + add each source.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v coral >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/coral" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "error: coral CLI not found. Install with:" >&2
    echo "  curl -fsSL https://withcoral.com/install.sh | sh" >&2
    exit 1
  fi
fi

echo "==> Coral version: $(coral --version)"
echo

echo "==> [1/3] Seeding demo JSONL data"
python3 scripts/seed_data.py
echo

echo "==> [2/3] Generating Coral source spec YAMLs"
python3 scripts/generate_source_specs.py
echo

echo "==> [3/3] Linting + installing demo sources"
for src in github_demo linear_demo slack_demo sentry_demo datadog_demo calendar_demo; do
  yaml="coral-sources/${src}.yaml"
  echo "  ▸ $src"
  coral source lint "$yaml" >/dev/null
  # Re-install if already present (coral source add is idempotent on file specs).
  if coral source list 2>/dev/null | awk 'NR>2{print $1}' | grep -qx "$src"; then
    coral source remove "$src" >/dev/null 2>&1 || true
  fi
  coral source add --file "$yaml" >/dev/null
  coral source test "$src" >/dev/null && echo "    ✓ tests passed"
done

echo
echo "==> Installed sources:"
coral source list

echo
echo "==> Sample query (cross-source JOIN):"
coral sql --format table "
  SELECT i.identifier, i.title AS linear_title, p.number AS pr, p.state
  FROM linear_demo.attachments a
  JOIN linear_demo.issues i ON i.id = a.issue_id
  JOIN github_demo.pulls p
    ON p.html_url = a.url
  ORDER BY p.number DESC
  LIMIT 5
"

echo
echo "Helmsman demo Coral sources installed. ⚓"
