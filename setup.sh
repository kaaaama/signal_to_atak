#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(pwd)"
SKIP_BUILD=0
SKIP_CERTS=0
FORCE_RECONFIGURE=0
NO_START_APP=0
REGENERATE_CERTS=0

SERVER_CERT_NAME=""
WEB_HOSTNAME=""

usage() {
  cat <<'USAGE'
Usage:
  ./setup.sh [--root PATH] [--skip-build] [--skip-certs] [--force-reconfigure] [--no-start-app] [--regenerate-certs]

Assumptions:
  - Repository is already cloned.
  - .env already exists and contains correct values.
  - TAK files are already present in:
      infra/tak/docker/
      infra/tak/tak/
  - docker-compose.yml already matches the current project layout.

What this script does:
  1. Validates the current repo layout.
  2. Loads .env safely, including passwords with special characters.
  3. Patches cert-metadata.sh for Ukraine / Kyiv / project-name defaults.
  4. Patches CoreConfig.xml from .env and replaces tak-database / tak-databasse with tak-db.
  5. Builds TAK services and the bot image (unless --skip-build).
  6. Starts tak-db, waits for health, then starts takserver.
  7. Generates missing TAK certs under infra/tak/tak/certs/files.
  8. Runs configureInDocker.sh when needed.
  9. Re-patches CoreConfig.xml after cert/config generation and restarts takserver.
 10. Tries to authorize admin.pem for the TAK web UI with retries.
 11. Imports admin.p12 into Chrome NSS DB if missing.
 12. Starts signal-cli-rest-api, postgres, and bot (unless --no-start-app).


Examples:
  ./setup.sh
  ./setup.sh --skip-build
  ./setup.sh --regenerate-certs --force-reconfigure
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="$2"
      shift 2
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --skip-certs)
      SKIP_CERTS=1
      shift
      ;;
    --force-reconfigure)
      FORCE_RECONFIGURE=1
      shift
      ;;
    --no-start-app)
      NO_START_APP=1
      shift
      ;;
    --regenerate-certs|--regenerate_certs)
      REGENERATE_CERTS=1
      FORCE_RECONFIGURE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

ROOT="$(cd "$ROOT" && pwd)"
export ROOT_PATH="$ROOT"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

warn() {
  printf '\n[WARN] %s\n' "$*" >&2
}

die() {
  printf '\n[ERROR] %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

compose() {
  (
    cd "$ROOT"
    BUILDKIT_PROGRESS=plain docker compose "$@"
  )
}

container_running() {
  local name="$1"
  docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q '^true$'
}

container_health() {
  local name="$1"
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || true
}

wait_for_container_running() {
  local name="$1"
  local attempts="${2:-30}"
  local sleep_sec="${3:-2}"

  for ((i=1; i<=attempts; i++)); do
    if container_running "$name"; then
      return 0
    fi
    sleep "$sleep_sec"
  done

  return 1
}

wait_for_container_healthy() {
  local name="$1"
  local attempts="${2:-60}"
  local sleep_sec="${3:-2}"

  for ((i=1; i<=attempts; i++)); do
    if [[ "$(container_health "$name")" == "healthy" ]]; then
      return 0
    fi
    sleep "$sleep_sec"
  done

  return 1
}

export_env_from_file() {
  eval "$(
    python3 - "$ROOT/.env" <<'PY'
from pathlib import Path
import shlex
import sys

env_path = Path(sys.argv[1])
if not env_path.exists():
    raise SystemExit(f"Missing env file: {env_path}")

for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue

    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()

    if (
        (value.startswith("'") and value.endswith("'")) or
        (value.startswith('"') and value.endswith('"'))
    ):
        value = value[1:-1]

    print(f"export {key}={shlex.quote(value)}")
PY
  )"
}

init_runtime_defaults() {
  SERVER_CERT_NAME="${TAK_SERVER_CERT_NAME:-${TAK_SERVER_HOSTNAME:-takserver}}"
  WEB_HOSTNAME="${TAK_WEB_HOSTNAME:-$SERVER_CERT_NAME}"
  export TAK_SERVER_CERT_NAME="$SERVER_CERT_NAME"
  export TAK_WEB_HOSTNAME="$WEB_HOSTNAME"
}

ensure_writable() {
  local path="$1"
  if [[ -w "$path" ]]; then
    return 0
  fi

  if command -v sudo >/dev/null 2>&1; then
    sudo chown "$USER:$USER" "$path" || die "Could not make $path writable"
  else
    die "$path is not writable and sudo is unavailable"
  fi
}

validate_repo_layout() {
  cp "$ROOT/infra/tak/tak/CoreConfig.example.xml" "$ROOT/infra/tak/tak/CoreConfig.xml"
  [[ -f "$ROOT/docker-compose.yml" ]] || die "Missing docker-compose.yml in $ROOT"
  [[ -f "$ROOT/.env" ]] || die "Missing .env in $ROOT"
  [[ -f "$ROOT/infra/tak/docker/Dockerfile.takserver" ]] || die "Missing $ROOT/infra/tak/docker/Dockerfile.takserver"
  [[ -f "$ROOT/infra/tak/docker/Dockerfile.takserver-db" ]] || die "Missing $ROOT/infra/tak/docker/Dockerfile.takserver-db"
  [[ -d "$ROOT/infra/tak/tak" ]] || die "Missing $ROOT/infra/tak/tak"
  [[ -f "$ROOT/infra/tak/tak/CoreConfig.xml" ]] || die "Missing $ROOT/infra/tak/tak/CoreConfig.xml"
}

check_env_keys() {
  local missing=0
  local keys=(
    PHONE_NUMBER
    SIGNAL_SERVICE
    SIGNAL_API_MODE
    SIGNAL_API_LOG_LEVEL
    DATABASE_URL
    TAK_HOST
    TAK_PORT
    TAK_SERVER_HOSTNAME
    TAK_CA_FILE
    TAK_CLIENT_CERT_FILE
    TAK_CLIENT_KEY_FILE
    TAK_POSTGRES_DB
    TAK_POSTGRES_USER
    TAK_POSTGRES_PASSWORD
  )

  for key in "${keys[@]}"; do
    if [[ -z "${!key:-}" ]]; then
      warn "Missing ${key} in .env"
      missing=1
    fi
  done

  if [[ "$missing" -eq 1 ]]; then
    warn "One or more expected keys are missing in .env. Continuing, but setup may fail later."
  fi
}

patch_cert_metadata() {
  local meta="$ROOT/infra/tak/tak/certs/cert-metadata.sh"
  [[ -f "$meta" ]] || { warn "cert-metadata.sh not found, skipping patch"; return; }

  ensure_writable "$meta"

  local project_name
  project_name="$(basename "$ROOT")"

  log "Patching cert-metadata.sh for Ukraine / Kyiv / ${project_name}"

  META_PATH="$meta" PROJECT_NAME="$project_name" python3 - <<'PY'
from pathlib import Path
import os
import re

path = Path(os.environ["META_PATH"])
project_name = os.environ["PROJECT_NAME"]

text = path.read_text(encoding="utf-8")

repls = {
    "COUNTRY": "UA",
    "STATE": "Kyiv",
    "CITY": "Kyiv",
    "ORGANIZATION": project_name,
    "ORGANIZATIONAL_UNIT": project_name,
}

for key, value in repls.items():
    pattern = rf'^(?:export\s+)?{key}=.*$'
    replacement = f'export {key}="{value}"'
    if re.search(pattern, text, flags=re.MULTILINE):
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += replacement + "\n"

path.write_text(text, encoding="utf-8")
print(f"Patched {path}")
PY
}

patch_core_config() {
  local core="$ROOT/infra/tak/tak/CoreConfig.xml"
  [[ -f "$core" ]] || { warn "CoreConfig.xml not found, skipping patch"; return; }

  ensure_writable "$core"

  log "Patching CoreConfig.xml from .env and replacing tak-database with tak-db"

  python3 - <<'PY'
from pathlib import Path
import html
import os
import re

root = Path(os.environ["ROOT_PATH"])
path = root / "infra" / "tak" / "tak" / "CoreConfig.xml"

db = os.environ.get("TAK_POSTGRES_DB") or os.environ.get("POSTGRES_DB")
user = os.environ.get("TAK_POSTGRES_USER") or os.environ.get("POSTGRES_USER")
password = os.environ.get("TAK_POSTGRES_PASSWORD") or os.environ.get("POSTGRES_PASSWORD")
port = os.environ.get("TAK_POSTGRES_PORT") or os.environ.get("POSTGRES_PORT") or "5432"

server_cert_name = os.environ.get("TAK_SERVER_CERT_NAME") or os.environ.get("TAK_SERVER_HOSTNAME") or "takserver"
web_hostname = os.environ.get("TAK_WEB_HOSTNAME") or server_cert_name

if not all([db, user, password]):
    raise SystemExit("Missing TAK_POSTGRES_* or POSTGRES_* values in environment")

text = path.read_text(encoding="utf-8")

text = text.replace("tak-database", "tak-db")
text = text.replace("tak-databasse", "tak-db")

escaped_user = html.escape(user, quote=True)
escaped_password = html.escape(password, quote=True)
escaped_web_host = html.escape(web_hostname, quote=True)

desired_url = f"jdbc:postgresql://tak-db:{port}/{db}"
desired_keystore = f"/opt/tak/certs/files/{server_cert_name}.jks"

text = re.sub(
    r'jdbc:postgresql://[^"<\s]+',
    desired_url,
    text,
)

text = re.sub(
    r'(<connection\b[^>]*\busername=")[^"]*(")',
    rf'\g<1>{escaped_user}\g<2>',
    text,
)

text = re.sub(
    r'(<connection\b[^>]*\bpassword=")[^"]*(")',
    rf'\g<1>{escaped_password}\g<2>',
    text,
)

text = re.sub(
    r'(<username>)[^<]*(</username>)',
    rf'\g<1>{escaped_user}\g<2>',
    text,
)

text = re.sub(
    r'(<password>)[^<]*(</password>)',
    rf'\g<1>{escaped_password}\g<2>',
    text,
)

text = re.sub(
    r'(<tls\b[^>]*\bkeystoreFile=")[^"]*(")',
    rf'\g<1>{desired_keystore}\g<2>',
    text,
)

text = re.sub(
    r'(<federation-server\b[^>]*\bwebBaseUrl=")https://[^"/:]+(?::\d+)?/Marti(")',
    rf'\g<1>https://{escaped_web_host}:8443/Marti\g<2>',
    text,
)

path.write_text(text, encoding="utf-8")
print(f"Patched {path}")
PY
}

build_images() {
  if [[ "$SKIP_BUILD" -eq 1 ]]; then
    log "Skipping image builds"
    return
  fi

  log "Building TAK images"
  compose build tak-db takserver

  log "Building bot image"
  compose build bot
}

start_tak_stack() {
  log "Starting tak-db"
  compose up -d tak-db

  log "Waiting for tak-db to become healthy"
  if ! wait_for_container_healthy tak-db 90 2; then
    die "tak-db did not become healthy. Check: docker compose logs tak-db"
  fi

  log "Starting takserver"
  compose up -d takserver

  if ! wait_for_container_running takserver 60 2; then
    die "takserver container did not reach running state. Check: docker compose logs takserver"
  fi
}

purge_existing_certs_if_requested() {
  if [[ "$REGENERATE_CERTS" -ne 1 ]]; then
    return
  fi

  local cert_dir="$ROOT/infra/tak/tak/certs/files"
  mkdir -p "$cert_dir"

  log "Regenerate certs requested; deleting existing generated cert files"

  rm -f     "$cert_dir"/admin.*     "$cert_dir"/phone1.*     "$cert_dir"/signal_sender.*     "$cert_dir"/takserver.*     "$cert_dir"/localhost.*     "$cert_dir"/"${SERVER_CERT_NAME}".*     "$cert_dir"/truststore-root.*     "$cert_dir"/fed-truststore.*     "$cert_dir"/ca.*     "$cert_dir"/*.jks     2>/dev/null || true
}

generate_missing_certs() {
  if [[ "$SKIP_CERTS" -eq 1 ]]; then
    log "Skipping certificate generation"
    return
  fi

  purge_existing_certs_if_requested

  local cert_dir="$ROOT/infra/tak/tak/certs/files"
  local server_jks="$cert_dir/${SERVER_CERT_NAME}.jks"
  mkdir -p "$cert_dir"

  local changed=0

  if [[ ! -f "$cert_dir/ca.pem" ]]; then
    log "Generating TAK root CA"
    compose exec takserver bash -lc "cd /opt/tak/certs && ./makeRootCa.sh"
    changed=1
  else
    log "Root CA already exists: $cert_dir/ca.pem"
  fi

  if [[ ! -f "$server_jks" ]]; then
    log "Generating TAK server certificate for '${SERVER_CERT_NAME}'"
    compose exec takserver bash -lc "cd /opt/tak/certs && ./makeCert.sh server '${SERVER_CERT_NAME}'"
    changed=1
  else
    log "TAK server certificate already exists: $server_jks"
  fi

  if [[ ! -f "$cert_dir/admin.pem" || ! -f "$cert_dir/admin.p12" ]]; then
    log "Generating TAK client certificate: admin"
    compose exec takserver bash -lc "cd /opt/tak/certs && ./makeCert.sh client admin"
    changed=1
  else
    log "Admin client certificate already exists"
  fi

  if [[ ! -f "$cert_dir/phone1.pem" || ! -f "$cert_dir/phone1.p12" ]]; then
    log "Generating TAK client certificate: phone1"
    compose exec takserver bash -lc "cd /opt/tak/certs && ./makeCert.sh client phone1"
    changed=1
  else
    log "Phone client certificate already exists"
  fi

  if [[ ! -f "$cert_dir/signal_sender.pem" || ! -f "$cert_dir/signal_sender.key" ]]; then
    log "Generating TAK client certificate: signal_sender"
    compose exec takserver bash -lc "cd /opt/tak/certs && ./makeCert.sh client signal_sender"
    changed=1
  else
    log "Signal sender client certificate already exists"
  fi
  sudo chown -R "$USER:$USER" "$ROOT/infra/tak/tak/certs/files"
  if [[ "$changed" -eq 1 || "$FORCE_RECONFIGURE" -eq 1 ]]; then
    log "Running configureInDocker.sh"
    compose exec takserver bash -lc "cd /opt/tak && ./configureInDocker.sh"
  else
    log "No cert changes detected and --force-reconfigure not set"
  fi
}

restart_takserver_if_needed() {
  log "Restarting takserver to pick up generated certs/config"
  compose restart takserver

  if ! wait_for_container_running takserver 60 2; then
    die "takserver did not come back after restart. Check: docker compose logs takserver"
  fi
}

nss_list_nicknames() {
  certutil -L -d sql:"$HOME/.pki/nssdb" 2>/dev/null \
    | awk '
        NR > 2 && NF {
          line = $0
          sub(/[[:space:]]+,.*$/, "", line)
          gsub(/[[:space:]]+$/, "", line)
          if (length(line)) print line
        }
      '
}

pem_sha256_fingerprint() {
  local pem="$1"
  openssl x509 -in "$pem" -noout -fingerprint -sha256 2>/dev/null \
    | awk -F= '{print $2}' \
    | tr -d ':'
}

nss_sha256_fingerprint_by_nick() {
  local nick="$1"
  local tmp
  tmp="$(mktemp)"

  if certutil -L -d sql:"$HOME/.pki/nssdb" -n "$nick" -a >"$tmp" 2>/dev/null; then
    openssl x509 -in "$tmp" -noout -fingerprint -sha256 2>/dev/null \
      | awk -F= '{print $2}' \
      | tr -d ':'
    rm -f "$tmp"
    return 0
  fi

  rm -f "$tmp"
  return 1
}

pem_subject_rfc2253() {
  local pem="$1"
  openssl x509 -in "$pem" -noout -subject -nameopt RFC2253 2>/dev/null \
    | sed 's/^subject=//'
}

nss_subject_rfc2253_by_nick() {
  local nick="$1"
  local tmp
  tmp="$(mktemp)"

  if certutil -L -d sql:"$HOME/.pki/nssdb" -n "$nick" -a >"$tmp" 2>/dev/null; then
    openssl x509 -in "$tmp" -noout -subject -nameopt RFC2253 2>/dev/null \
      | sed 's/^subject=//'
    rm -f "$tmp"
    return 0
  fi

  rm -f "$tmp"
  return 1
}

find_nss_nick_by_pem() {
  local pem="$1"
  [[ -f "$pem" ]] || return 1

  local want_fp
  want_fp="$(pem_sha256_fingerprint "$pem")"
  [[ -n "$want_fp" ]] || return 1

  local nick
  while IFS= read -r nick; do
    [[ -n "$nick" ]] || continue

    local got_fp
    got_fp="$(nss_sha256_fingerprint_by_nick "$nick" || true)"

    if [[ -n "$got_fp" && "$got_fp" == "$want_fp" ]]; then
      printf '%s\n' "$nick"
      return 0
    fi
  done < <(nss_list_nicknames)

  return 1
}

delete_nss_certs_with_same_subject_as_pem() {
  local pem="$1"
  [[ -f "$pem" ]] || return 0

  local want_subject
  want_subject="$(pem_subject_rfc2253 "$pem")"
  [[ -n "$want_subject" ]] || return 0

  local nick
  while IFS= read -r nick; do
    [[ -n "$nick" ]] || continue

    local got_subject
    got_subject="$(nss_subject_rfc2253_by_nick "$nick" || true)"

    if [[ -n "$got_subject" && "$got_subject" == "$want_subject" ]]; then
      log "Removing old Chrome NSS certificate: $nick"
      certutil -D -d sql:"$HOME/.pki/nssdb" -n "$nick" >/dev/null 2>&1 || true
    fi
  done < <(nss_list_nicknames)
}

import_admin_p12_chrome() {
  local p12="$ROOT/infra/tak/tak/certs/files/admin.p12"
  local pem="$ROOT/infra/tak/tak/certs/files/admin.pem"

  [[ -f "$p12" ]] || { warn "admin.p12 not found, skipping Chrome NSS import"; return; }
  [[ -f "$pem" ]] || { warn "admin.pem not found, skipping Chrome NSS import"; return; }

  if ! command -v pk12util >/dev/null 2>&1; then
    warn "pk12util not found; install libnss3-tools if you want automatic Chrome/NSS import"
    return
  fi

  if ! command -v certutil >/dev/null 2>&1; then
    warn "certutil not found; install libnss3-tools if you want automatic Chrome/NSS import"
    return
  fi

  if ! command -v openssl >/dev/null 2>&1; then
    warn "openssl not found; skipping automatic Chrome/NSS import"
    return
  fi

  mkdir -p "$HOME/.pki/nssdb"

  if [[ ! -f "$HOME/.pki/nssdb/cert9.db" ]]; then
    certutil -N -d sql:"$HOME/.pki/nssdb" --empty-password >/dev/null 2>&1 || true
  fi

  if [[ "$REGENERATE_CERTS" -eq 1 ]]; then
    delete_nss_certs_with_same_subject_as_pem "$pem"
  fi

  local existing_nick=""
  existing_nick="$(find_nss_nick_by_pem "$pem" || true)"

  if [[ -n "$existing_nick" ]]; then
    log "admin certificate already present in Chrome NSS DB as: $existing_nick"
    return
  fi

  log "Importing admin.p12 into Chrome NSS DB"
  P12_PASSWORD="${TAK_P12_PASSWORD:-atakatak}"
  if ! pk12util -d sql:"$HOME/.pki/nssdb" -i "$p12" -W "$P12_PASSWORD"; then
    warn "pk12util import failed. Most often this means:"
    warn "  - the PKCS#12 password entered was wrong, or"
    warn "  - an older conflicting certificate is still in NSS DB."
    warn "Current NSS certificates:"
    certutil -L -d sql:"$HOME/.pki/nssdb" || true
  fi
}

run_alembic_migrations() {
  log "Running Alembic migrations"

  if ! compose run --rm --no-deps bot alembic upgrade head; then
    die "Alembic migration failed"
  fi
}

start_app_stack() {
  if [[ "$NO_START_APP" -eq 1 ]]; then
    log "Skipping startup of signal-cli-rest-api, postgres, and bot"
    return
  fi

  log "Starting signal-cli-rest-api and postgres"
  compose up -d signal-cli-rest-api postgres

  log "Waiting for postgres to become healthy"
  if ! wait_for_container_healthy postgres 90 2; then
    die "postgres did not become healthy. Check: docker compose logs postgres"
  fi

  run_alembic_migrations

  log "Starting bot"
  compose up -d bot
}

print_summary() {
  local cert_dir="$ROOT/infra/tak/tak/certs/files"

  cat <<EOF_SUMMARY

Setup completed.

Project root:
  $ROOT

Important cert files:
  Browser admin cert:  $cert_dir/admin.p12
  ATAK truststore:     $cert_dir/truststore-root.p12
  ATAK client cert:    $cert_dir/phone1.p12
  Bot CA file:         $cert_dir/ca.pem
  Bot client cert:     $cert_dir/signal_sender.pem
  Bot client key:      $cert_dir/signal_sender.key
  Server keystore:     $cert_dir/${SERVER_CERT_NAME}.jks

TAK web UI:
  https://${WEB_HOSTNAME}:8443/setup/

Manual steps still required:
  1. If Chrome import failed, import admin.p12 manually or via pk12util.
  2. Copy truststore-root.p12 and phone1.p12 to your phone.
  3. In ATAK: Settings -> General Settings -> Network Settings:
       - SSL/TLS Truststore -> truststore-root.p12
       - Client Certificate -> phone1.p12
     Then add the server using your VM IP on port 8089.
  4. Run Alembic if needed:
       docker compose run --rm bot alembic upgrade head
  5. If you changed TAK_POSTGRES_* on an existing install, recreate the tak_db_data volume once.
  6. Send a Signal message like:
       35.000000 48.450000 alpha
EOF_SUMMARY
}

main() {
  require_cmd docker
  require_cmd grep
  require_cmd awk
  require_cmd python3
  require_cmd openssl

  docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is required (docker compose ...)"

  log "Validating repo layout"
  validate_repo_layout

  log "Loading .env safely"
  export_env_from_file
  init_runtime_defaults

  log "Checking .env for expected keys"
  check_env_keys

  patch_core_config
  patch_cert_metadata
  build_images
  start_tak_stack
  generate_missing_certs
  restart_takserver_if_needed
  import_admin_p12_chrome
  start_app_stack
  print_summary
}

main "$@"