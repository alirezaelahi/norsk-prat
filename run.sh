#!/usr/bin/env bash
# One-command start: installs dependencies, downloads models (first run only), opens the app.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Prat needs a Mac with Apple silicon (M1 or newer): the models run on MLX." >&2
  exit 1
fi
if ! command -v uv >/dev/null; then
  echo "This project uses 'uv' to manage Python. Install it with:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

uv sync --quiet
uv run --quiet prat setup
exec uv run --quiet prat --open "$@"
