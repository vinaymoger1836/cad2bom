#!/usr/bin/env bash
# Deploy CAD-to-BoM on a small Linux VM (Ubuntu/Debian, x86_64 or arm64) with HTTPS.
#
#   git clone <your repository> && cd cad2bom
#   sudo -v && C2B_DOMAIN=bom.example.com C2B_ACME_EMAIL=you@example.com bash scripts/deploy.sh
#
# What it does, in order (safe to run again; it never overwrites an existing .env value):
#   1. checks the machine (OS, CPU, memory, ports) and installs Docker if it is missing
#   2. creates .env with random secrets and sets the production values
#   3. sets the admin passcode and the admin web login (random ones are printed ONCE, at the end)
#   4. builds the images for this machine's CPU and starts the stack with automatic HTTPS
#   5. installs a nightly backup and a 5-minute health check (systemd timers)
#   6. waits for the site to answer and prints what to do next
#
# Options (environment variables):
#   C2B_DOMAIN          required, e.g. bom.example.com (its DNS record must point at this machine)
#   C2B_ACME_EMAIL      optional, for certificate expiry notices
#   C2B_ADMIN_PASSCODE  optional; random if not set and not already in .env
#   C2B_ADMIN_BASIC_PASSWORD  optional; random if not set and no hash is in .env
#   C2B_ENABLE_SWAP=1   create a 2 GB swap file on machines with less than 6 GB RAM
#   C2B_SKIP_TIMERS=1   do not install the systemd timers
#
# Requirements: ports 80 and 443 open to the internet, about 10 GB free disk, 4 GB RAM or more
# (the CAD reader may use up to 3 GB).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

# --- 1. the machine ---------------------------------------------------------------------------
say "Checking this machine"
[ "$(uname -s)" = "Linux" ] || die "this script is for Linux servers"
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) die "unsupported CPU $(uname -m) (need x86_64 or arm64)" ;;
esac
echo "CPU: $ARCH"
[ -n "${C2B_DOMAIN:-}" ] || die "set C2B_DOMAIN (for example C2B_DOMAIN=bom.example.com)"
case "$C2B_DOMAIN" in
  *[!A-Za-z0-9.-]*|"") die "C2B_DOMAIN must be a plain host name like bom.example.com" ;;
esac

MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
echo "Memory: ${MEM_MB} MB"
if [ "$MEM_MB" -lt 3500 ]; then
  die "less than 4 GB of memory: the CAD reader needs up to 3 GB. Use a larger machine."
fi
if [ "$MEM_MB" -lt 6000 ] && [ "${C2B_ENABLE_SWAP:-0}" = "1" ] && [ ! -f /swapfile ]; then
  say "Creating a 2 GB swap file"
  $SUDO fallocate -l 2G /swapfile && $SUDO chmod 600 /swapfile && $SUDO mkswap /swapfile && $SUDO swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
fi

FREE_GB=$(df -Pk "$ROOT" | awk 'NR==2 {print int($4/1024/1024)}')
[ "$FREE_GB" -ge 8 ] || die "only ${FREE_GB} GB of free disk here; about 10 GB are needed for the images"

if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker (official script from get.docker.com)"
  command -v curl >/dev/null 2>&1 || { $SUDO apt-get update -y && $SUDO apt-get install -y curl; }
  TMP=$(mktemp)
  curl -fsSL https://get.docker.com -o "$TMP"
  $SUDO sh "$TMP"
  rm -f "$TMP"
fi
docker compose version >/dev/null 2>&1 || die "docker compose plugin is missing; install docker-compose-plugin"
DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="$SUDO docker"
if [ "$DOCKER" != "docker" ]; then
  echo "note: using sudo for docker. Add yourself to the docker group to avoid it: sudo usermod -aG docker \$USER"
fi

# --- 2. .env ----------------------------------------------------------------------------------
say "Preparing .env"
command -v python3 >/dev/null 2>&1 || die "python3 is needed (sudo apt-get install -y python3)"
python3 scripts/init_env.py

# setenv KEY VALUE [force]: set KEY in .env unless it already has a non-empty value.
setenv() {
  python3 - "$1" "$2" "${3:-}" <<'PY'
import re, sys
from pathlib import Path
key, value, force = sys.argv[1], sys.argv[2], sys.argv[3] == "force"
p = Path(".env")
lines = p.read_text(encoding="utf-8").splitlines()
out, seen = [], False
for ln in lines:
    m = re.match(rf"^#?\s*{re.escape(key)}=(.*)$", ln)
    if m:
        seen = True
        current = m.group(1).strip().strip("'\"")
        if ln.startswith("#") or not current or force:
            out.append(f"{key}={value}")
        else:
            out.append(ln)
    else:
        out.append(ln)
if not seen:
    out.append(f"{key}={value}")
p.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}
getenv_val() { sed -n "s/^$1=//p" .env | tail -1 | sed "s/^'//; s/'\$//"; }

setenv C2B_ENV production force
setenv C2B_DOMAIN "$C2B_DOMAIN" force
[ -z "${C2B_ACME_EMAIL:-}" ] || setenv C2B_ACME_EMAIL "$C2B_ACME_EMAIL" force

# --- 3. admin ---------------------------------------------------------------------------------
say "Admin access"
PRINT_PASSCODE=""
PRINT_BASIC=""
if [ -z "$(getenv_val C2B_ADMIN_PASSCODE)" ]; then
  PASS=${C2B_ADMIN_PASSCODE:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')}
  setenv C2B_ADMIN_PASSCODE "$PASS" force
  PRINT_PASSCODE="$PASS"
fi
setenv C2B_ADMIN_BASIC_USER admin
if [ -z "$(getenv_val C2B_ADMIN_BASIC_HASH)" ]; then
  BASIC=${C2B_ADMIN_BASIC_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')}
  HASH=$($DOCKER run --rm caddy:2.10-alpine caddy hash-password --plaintext "$BASIC")
  setenv C2B_ADMIN_BASIC_HASH "'$HASH'" force
  PRINT_BASIC="$BASIC"
fi
chmod 600 .env

# --- 4. build and start -----------------------------------------------------------------------
say "Building images for $ARCH and starting the stack (the first build takes several minutes)"
COMPOSE="$DOCKER compose --env-file .env -f infra/docker-compose.yml -f infra/docker-compose.prod.yml"
mkdir -p data
$COMPOSE up -d --build --wait

# --- 5. timers --------------------------------------------------------------------------------
if [ "${C2B_SKIP_TIMERS:-0}" != "1" ] && command -v systemctl >/dev/null 2>&1; then
  say "Installing the nightly backup and the health check"
  RUN_USER=$(id -un)
  for unit in c2b-backup.service c2b-backup.timer c2b-health.service c2b-health.timer; do
    sed -e "s|__APP_DIR__|$ROOT|g" -e "s|__USER__|$RUN_USER|g" "infra/deploy/$unit" \
      | $SUDO tee "/etc/systemd/system/$unit" >/dev/null
  done
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now c2b-backup.timer c2b-health.timer
fi

# --- 6. wait and report -----------------------------------------------------------------------
say "Waiting for https://$C2B_DOMAIN (the certificate can take a minute)"
OK=0
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null --max-time 10 "https://$C2B_DOMAIN/api/readyz"; then OK=1; break; fi
  sleep 5
done

echo
if [ "$OK" = "1" ]; then
  echo "CAD-to-BoM is running at https://$C2B_DOMAIN"
else
  echo "The stack is up, but https://$C2B_DOMAIN did not answer yet. Check that the DNS record points"
  echo "here and that ports 80 and 443 are open, then look at: $COMPOSE logs caddy"
fi
if [ -n "$PRINT_PASSCODE$PRINT_BASIC" ]; then
  echo
  echo "Admin page: https://$C2B_DOMAIN/admin   (shown ONCE; store them in a password manager)"
  [ -z "$PRINT_BASIC" ] || echo "  web login   user: admin   password: $PRINT_BASIC"
  [ -z "$PRINT_PASSCODE" ] || echo "  passcode:   $PRINT_PASSCODE"
fi
echo
echo "Next: set APS_CLIENT_ID / APS_CLIENT_SECRET in .env to allow SolidWorks and Fusion files"
echo "(free tier only; C2B_APS_MONTHLY_CAP stops it), then run: $COMPOSE up -d"
echo "Backups: backups/ (nightly, kept $(getenv_val C2B_BACKUP_KEEP)); restore with: sh scripts/restore.sh backups/<name>"
