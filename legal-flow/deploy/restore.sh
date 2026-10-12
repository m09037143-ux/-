#!/bin/sh
# Восстановление базы из дампа (например, из бэкапа или дампа с Render):
#   ./restore.sh путь/к/дампу.sql.gz
# ВНИМАНИЕ: полностью заменяет текущее содержимое базы. Приложение и воркер на время останавливаются.
set -eu
cd "$(dirname "$0")"
dump="${1:-}"
if [ -z "$dump" ] || [ ! -f "$dump" ]; then
  echo "Использование: ./restore.sh путь/к/дампу.sql.gz" >&2
  exit 1
fi
printf "Заменить ВСЮ базу содержимым %s? Введите yes: " "$dump"
read -r answer
[ "$answer" = "yes" ] || { echo "Отменено"; exit 1; }

docker compose up -d db
docker compose stop app worker caddy 2>/dev/null || true
docker compose exec -T db psql -U pravovoy_potok -d postgres -c "DROP DATABASE IF EXISTS pravovoy_potok WITH (FORCE);"
docker compose exec -T db psql -U pravovoy_potok -d postgres -c "CREATE DATABASE pravovoy_potok OWNER pravovoy_potok;"
gunzip -c "$dump" | docker compose exec -T db psql -U pravovoy_potok -d pravovoy_potok -v ON_ERROR_STOP=1 -q
docker compose up -d
echo "Готово. Проверьте: docker compose ps"
