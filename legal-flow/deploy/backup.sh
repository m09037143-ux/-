#!/bin/sh
# Резервная копия базы: сжатый дамп в deploy/backups/, копии старше 14 дней удаляются.
# Ставится в cron раз в сутки (см. README, шаг «Резервные копии»). Запуск вручную: ./backup.sh
set -eu
cd "$(dirname "$0")"
mkdir -p backups
file="backups/pp_$(date +%Y%m%d_%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U pravovoy_potok --no-owner --no-acl pravovoy_potok | gzip > "$file"
# пустой/битый файл — признак сбоя
if [ ! -s "$file" ] || [ "$(wc -c < "$file")" -lt 200 ]; then
  echo "Резервная копия не создана: $file слишком мал" >&2
  rm -f "$file"
  exit 1
fi
find backups -name 'pp_*.sql.gz' -mtime +14 -delete
echo "OK: $file ($(du -h "$file" | cut -f1))"
