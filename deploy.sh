#!/usr/bin/env bash
#
# Settlement Portal deployment helper.
#
# Every command needs root, so run it either way:
#   sudo ./deploy.sh
#   ./deploy.sh
#
# The script detects whether it is already running as root and only adds the
# sudo prefix when needed, so commands are never double-prefixed.
#
# Usage:
#   sudo ./deploy.sh                # pull, build, start, then ask about migrations
#   sudo ./deploy.sh --migrate      # pull, build, start, and run migrations
#   sudo ./deploy.sh --no-migrate   # pull, build, start, skip migrations
#
# The Git username defaults to "pranta". Git still prompts for the password
# (or uses your configured credential helper). No password is stored here.
#
# Override the username if needed:
#   sudo env GIT_USERNAME=another-user ./deploy.sh

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

GIT_USERNAME="${GIT_USERNAME:-pranta}"
MIGRATION_MODE="ask"

# Every command requires sudo. If the script was started with `sudo`, we are
# already root and must not prefix commands again.
if [[ "${EUID}" -eq 0 ]]; then
  SUDO=()
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "This script requires root. Re-run with: sudo ./deploy.sh" >&2
    exit 1
  fi
  SUDO=(sudo)
fi

# Run a command as root. Using a function keeps the script compatible with
# older bash versions where expanding an empty array under `set -u` fails.
run() {
  if [[ "${#SUDO[@]}" -gt 0 ]]; then
    "${SUDO[@]}" "$@"
  else
    "$@"
  fi
}

usage() {
  cat <<'EOF'
Usage:
  sudo ./deploy.sh                Pull, build, start, then ask about migrations.
  sudo ./deploy.sh --migrate      Pull, build, start, and run migrations.
  sudo ./deploy.sh --no-migrate   Pull, build, start, and skip migrations.
  sudo ./deploy.sh --help         Show this help.

  ./deploy.sh also works; the script adds sudo automatically when it is not
  already running as root.

Environment:
  GIT_USERNAME                    Git username to use (default: pranta).
EOF
}

for arg in "$@"; do
  case "${arg}" in
    --migrate) MIGRATION_MODE="yes" ;;
    --no-migrate) MIGRATION_MODE="no" ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: ${arg}" >&2
      usage >&2
      exit 2
      ;;
  esac
done

log() {
  printf '\n==> %s\n' "$*"
}

if [[ "${#SUDO[@]}" -gt 0 ]]; then
  log "Requesting sudo access"
  sudo -v
fi

if ! run env git --version >/dev/null 2>&1; then
  echo "git is required but was not found" >&2
  exit 1
fi

if ! run docker compose version >/dev/null 2>&1; then
  echo "docker compose is required but was not found" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "warning: .env not found; Docker Compose will use environment variables only" >&2
fi

log "Pulling latest code (Git username: ${GIT_USERNAME})"
# GIT_TERMINAL_PROMPT=1 keeps Git's username/password prompt enabled even when
# no credential helper is configured. The username comes from -c, so Git only
# prompts for the password.
run env GIT_TERMINAL_PROMPT=1 git \
  -c credential.username="${GIT_USERNAME}" \
  pull --ff-only

log "Building Docker image"
run docker compose build

log "Starting containers"
run docker compose up -d

if [[ "${MIGRATION_MODE}" == "ask" ]]; then
  reply=""
  if [[ -t 0 ]]; then
    read -r -p "Run database migrations now? [y/N] " reply || true
  fi
  case "${reply}" in
    y|Y|yes|YES|Yes) MIGRATION_MODE="yes" ;;
    *) MIGRATION_MODE="no" ;;
  esac
fi

if [[ "${MIGRATION_MODE}" == "yes" ]]; then
  log "Applying database migrations"
  run docker compose run --rm --no-deps web python manage.py migrate --database default
else
  log "Skipping database migrations"
fi

log "Current container status"
run docker compose ps

log "Deployment complete"
