#!/usr/bin/env bash
# One-command local setup: Postgres (Docker) + Python venv + deps + .env.
# Idempotent and self-contained; it will even start Docker Desktop for you.
# The only hard requirement is that Docker is installed.
set -euo pipefail
cd "$(dirname "$0")"

log() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
err() { printf '\033[1;31mERROR:\033[0m %s\n' "$1" >&2; }

# --- 1. locate the docker CLI (PATH, else common Docker Desktop / brew paths) ---
find_docker() {
  command -v docker 2>/dev/null && return 0
  for p in \
    "/Applications/Docker.app/Contents/Resources/bin/docker" \
    "/usr/local/bin/docker" "/opt/homebrew/bin/docker"; do
    [ -x "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}
DOCKER="$(find_docker)" || { err "docker CLI not found. Install Docker Desktop."; exit 1; }
export PATH="$(dirname "$DOCKER"):$PATH"
log "docker: $DOCKER ($(docker --version))"

# --- 2. ensure the docker daemon is up (try to start it if it isn't) ---
if ! docker info >/dev/null 2>&1; then
  log "Docker daemon not running; trying to start it…"
  case "$(uname -s)" in
    Darwin) open -a Docker >/dev/null 2>&1 || true ;;
    Linux)  sudo systemctl start docker >/dev/null 2>&1 || true ;;
  esac
  log "Waiting for the Docker daemon (up to ~180s)…"
  for _ in $(seq 1 90); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { err "Docker daemon never came up. Start Docker and re-run."; exit 1; }
fi
log "Docker daemon is up."

# --- 3. Postgres ---
log "Starting Postgres (docker compose up -d)…"
docker compose up -d
log "Waiting for Postgres to accept connections…"
for _ in $(seq 1 30); do
  docker compose exec -T db pg_isready -U kinetic >/dev/null 2>&1 && break
  sleep 1
done

# --- 4. Python deps (venv + pip) ---
PY=""
for c in python3.14 python3 python; do command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }; done
[ -n "$PY" ] || { err "No Python interpreter found."; exit 1; }
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
log "Python: $PY ($PYVER)"
case "$PYVER" in
  3.14|3.1[5-9]|3.[2-9][0-9]) : ;;
  *) log "WARNING: project targets Python 3.14; you have $PYVER (deps may not resolve)";;
esac
[ -d .venv ] || { log "Creating venv (.venv)…"; "$PY" -m venv .venv; }
log "Installing deps with pip…"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt
RUN_PREFIX="./.venv/bin"

# --- 5. .env ---
[ -f .env ] || { log "Creating .env from .env.example…"; cp .env.example .env; }

# --- 6. migrations (schema, before seed) ---
log "Applying migrations (alembic upgrade head)…"
./.venv/bin/alembic upgrade head

# --- 7. seed (idempotent; builds the Sara world via the application services) ---
log "Seeding the database (python -m scripts.seed)…"
./.venv/bin/python -m scripts.seed

log "Setup complete. Postgres is running, deps installed, database seeded."
echo "    Start the API:  ./.venv/bin/uvicorn app.main:app --reload"
