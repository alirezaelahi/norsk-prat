#!/usr/bin/env bash
# Start Prat. First run downloads models (~6 GB) into the Hugging Face cache and ./models.
set -euo pipefail
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/"; exit 1; }
uv sync --extra mlx -q
exec uv run python -m prat "$@"
