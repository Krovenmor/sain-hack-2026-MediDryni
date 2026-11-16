#!/bin/sh
# Точка входа контейнера: сервер должен слушать 0.0.0.0 и порт из переменной PORT.
set -eu
project_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python -m uvicorn main:app --app-dir "$project_dir/services/test" --host 0.0.0.0 --port "${PORT:-8000}"
