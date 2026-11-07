#!/usr/bin/env sh
set -eu
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_ROOT/backend"
exec "$PROJECT_ROOT/.venv/bin/python" -m app.execution.worker "$@"
