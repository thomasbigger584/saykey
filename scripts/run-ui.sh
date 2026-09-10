#!/usr/bin/env bash
# Launch the Saykey desktop UI (macOS / Linux).
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="$root/.venv/bin/python"

if [ ! -x "$py" ]; then
    echo "No .venv found. Create one and 'pip install -r recorder/requirements.txt' first." >&2
    exit 1
fi
if ! "$py" -c "import PySide6" 2>/dev/null; then
    echo "Installing UI dependencies (PySide6) ..."
    "$py" -m pip install -r "$root/ui/requirements.txt"
fi

cd "$root"
exec "$py" -m ui "$@"
