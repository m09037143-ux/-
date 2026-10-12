#!/bin/sh
# Обновление сайта до последней версии кода и перезапуск (миграции базы применяются при старте приложения).
# Перед обновлением автоматически делается резервная копия базы.
set -eu
cd "$(dirname "$0")"
./backup.sh
git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
docker compose ps
