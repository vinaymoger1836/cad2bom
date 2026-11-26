#!/bin/sh
# Back up the stack: a database dump, the job files and the library store (published versions).
# Run from the repository root: `make backup` or `sh scripts/backup.sh`.
#
#   backups/<UTC timestamp>/db.sql.gz       pg_dump of the whole database
#   backups/<UTC timestamp>/files.tar.gz    data/jobs, data/library (not data/out: it is rebuilt)
#   backups/<UTC timestamp>/MANIFEST        what was saved, sizes and checksums
#
# Keeps the newest C2B_BACKUP_KEEP backups (default 14). The .env file is NOT included: it holds
# secrets, so keep your own copy somewhere safe (a password manager is fine).
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
[ -f .env ] || { echo "no .env here; run from the repository root" >&2; exit 1; }
KEEP=$(sed -n 's/^C2B_BACKUP_KEEP=//p' .env | tail -1)
KEEP=${KEEP:-14}
COMPOSE="docker compose --env-file .env -f infra/docker-compose.yml"
[ -f infra/docker-compose.prod.yml ] && [ "${C2B_ENV_FILE_PROD:-}" = "1" ] && \
  COMPOSE="$COMPOSE -f infra/docker-compose.prod.yml"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="backups/$STAMP"
mkdir -p "$DEST"
umask 077

echo "dumping the database..."
$COMPOSE exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --clean --if-exists' \
  | gzip -9 > "$DEST/db.sql.gz"

echo "archiving job files and the library store..."
mkdir -p data/jobs data/library
tar -czf "$DEST/files.tar.gz" data/jobs data/library

{
  echo "created: $STAMP"
  echo "files:"
  (cd "$DEST" && ls -l db.sql.gz files.tar.gz && sha256sum db.sql.gz files.tar.gz 2>/dev/null || shasum -a 256 db.sql.gz files.tar.gz)
} > "$DEST/MANIFEST"

# A dump that is only a few bytes means pg_dump failed quietly.
SIZE=$(wc -c < "$DEST/db.sql.gz")
if [ "$SIZE" -lt 200 ]; then
  echo "backup failed: the database dump is empty" >&2
  rm -rf "$DEST"
  exit 1
fi

# Rotate: delete all but the newest $KEEP.
COUNT=$(ls -1d backups/*/ 2>/dev/null | wc -l)
if [ "$COUNT" -gt "$KEEP" ]; then
  ls -1d backups/*/ | head -n "$((COUNT - KEEP))" | while read -r old; do
    echo "removing old backup $old"
    rm -rf "$old"
  done
fi
echo "backup saved in $DEST"
