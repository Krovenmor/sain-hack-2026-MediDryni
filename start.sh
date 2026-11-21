#!/bin/sh
# Точка входа контейнера: сервер должен слушать 0.0.0.0 и порт из переменной PORT.
set -eu
project_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export DEMO_ACCESS_CODE='556123'
exec python -m uvicorn concierge_service.cmd.api.main:app \
  --app-dir "$project_dir/src" \
  --no-access-log \
  --host 0.0.0.0 --port "${PORT:-8000}"
