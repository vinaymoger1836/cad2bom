#!/bin/sh
# Check the running stack from outside (cron, an uptime service, or `make health`).
# Exit 0 = healthy, 1 = something is wrong. On failure, if C2B_ALERT_WEBHOOK is set (in .env or the
# environment), a text message is POSTed to it. Checks: the front door, the API's readiness (database,
# disk space, library store), and that every container is running.
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
[ -f .env ] && {
  PORT=$(sed -n 's/^C2B_HTTP_PORT=//p' .env | tail -1)
  HOOK=$(sed -n 's/^C2B_ALERT_WEBHOOK=//p' .env | tail -1)
}
PORT=${PORT:-8080}
HOOK=${C2B_ALERT_WEBHOOK:-${HOOK:-}}
BASE=${C2B_HEALTH_URL:-http://127.0.0.1:$PORT}
PROBLEMS=""

add() { PROBLEMS="$PROBLEMS
- $1"; }

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/api/readyz" || echo 000)
[ "$code" = "200" ] || add "readiness check returned $code (the API, database, disk or library store)"

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/" || echo 000)
[ "$code" = "200" ] || add "front page returned $code"

if command -v docker >/dev/null 2>&1; then
  COMPOSE="docker compose --env-file .env -f infra/docker-compose.yml"
  [ -f infra/docker-compose.prod.yml ] && [ "${C2B_PROD:-}" = "1" ] && COMPOSE="$COMPOSE -f infra/docker-compose.prod.yml"
  for svc in postgres api worker cad-worker caddy; do
    state=$($COMPOSE ps --format '{{.State}} {{.Health}}' "$svc" 2>/dev/null | head -1)
    case "$state" in
      running*unhealthy*|"") add "service $svc is not healthy ($state)" ;;
      running*) ;;
      *) add "service $svc is not running ($state)" ;;
    esac
  done
fi

if [ -z "$PROBLEMS" ]; then
  echo "healthy"
  exit 0
fi

MSG="CAD-to-BoM health check failed on $(hostname):$PROBLEMS"
echo "$MSG" >&2
if [ -n "$HOOK" ]; then
  curl -s -o /dev/null --max-time 10 -X POST -H 'Content-Type: text/plain' --data "$MSG" "$HOOK" || true
fi
exit 1
