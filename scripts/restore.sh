#!/bin/sh
# Restore a backup made by scripts/backup.sh. Usage: sh scripts/restore.sh backups/<timestamp>
#
# This REPLACES the database and the job/library files with the backup. It asks for confirmation
# unless C2B_RESTORE_YES=1 is set. The stack is stopped while files are restored, then started.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
SRC=${1:-}
[ -n "$SRC" ] && [ -f "$SRC/db.sql.gz" ] && [ -f "$SRC/files.tar.gz" ] || {
  echo "usage: sh scripts/restore.sh backups/<timestamp>   (needs db.sql.gz and files.tar.gz)" >&2
  exit 1
}
COMPOSE="docker compose --env-file .env -f infra/docker-compose.yml"

if [ "${C2B_RESTORE_YES:-}" != "1" ]; then
  printf 'This replaces the current database and job files with %s. Type "restore" to continue: ' "$SRC"
  read -r answer
  [ "$answer" = "restore" ] || { echo "cancelled"; exit 1; }
fi

echo "stopping the stack's app services..."
$COMPOSE stop caddy api worker cad-worker >/dev/null 2>&1 || true
$COMPOSE up -d postgres --wait

echo "restoring the database..."
gunzip -c "$SRC/db.sql.gz" | $COMPOSE exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null

echo "restoring job files and the library store..."
rm -rf data/jobs data/library
tar -xzf "$SRC/files.tar.gz"

echo "starting the stack..."
$COMPOSE up -d --wait
echo "restored from $SRC"
