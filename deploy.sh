#!/usr/bin/env bash
# Скат: provision and (re)deploy the whole stack on a fresh or existing Debian/Ubuntu server.
#
#   export GITHUB_TOKEN='<fine-grained PAT, Contents: read-only>'   # only if the repo is private
#   sudo -E ./deploy.sh
#
# Idempotent: each run checks what is already in place and changes only what is missing or
# outdated. It never prints secrets, never writes the GitHub token to disk and never publishes
# the backend port to the Internet.
#
# Result:
#   https://scat-search.ru      -> Caddy (host, systemd) -> 127.0.0.1:8080 -> frontend (Nginx)
#   https://www.scat-search.ru  -> 308 redirect to https://scat-search.ru
#   https://api.scat-search.ru  -> Caddy (host, systemd) -> 127.0.0.1:8000 -> backend (FastAPI)
#
# Settings (environment variables, all optional):
#   GITHUB_TOKEN            PAT used only for git clone/fetch; asked for when needed and a TTY exists
#   SCAT_REPO_URL           default https://github.com/Pro100kir2/scat.git
#   SCAT_BRANCH             default main
#   SCAT_DIR                default /opt/scat
#   SCAT_DOMAIN             default scat-search.ru       (www.<domain> redirects to it)
#   SCAT_API_DOMAIN         default api.<SCAT_DOMAIN>
#   CADDY_EMAIL             e-mail for Let's Encrypt expiry notices (optional)
#   ENABLE_FIREWALL=1       install/enable ufw when no firewall is active (default: leave as is)
#   MIGRATE_LEGACY=1        stop an older Скат install that holds port 8000/8080 without asking
#   CADDY_TAKEOVER=1        replace a custom /etc/caddy/Caddyfile that already serves the domains
#   DOCKER_REGISTRY_MIRROR  e.g. https://mirror.gcr.io, if Docker Hub is unreachable from the server
#   STRICT_DNS=1            treat "DNS not pointing here yet" as a failure (default: warning)

set -Eeuo pipefail
# Files copied into images must stay world-readable: the containers run as non-root users.
umask 022

# ------------------------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------------------------
REPO_URL="${SCAT_REPO_URL:-https://github.com/Pro100kir2/scat.git}"
BRANCH="${SCAT_BRANCH:-main}"
APP_DIR="${SCAT_DIR:-/opt/scat}"
DOMAIN="${SCAT_DOMAIN:-scat-search.ru}"
API_DOMAIN="${SCAT_API_DOMAIN:-api.${DOMAIN}}"
WWW_DOMAIN="www.${DOMAIN}"
BACKEND_LOCAL="127.0.0.1:8000"
FRONTEND_LOCAL="127.0.0.1:8080"
CADDY_SITES_DIR="/etc/caddy/sites"
CADDY_SITE_FILE="${CADDY_SITES_DIR}/scat.caddy"
LOG_FILE="/var/log/scat-deploy.log"
TOTAL_STEPS=11

# ------------------------------------------------------------------------------------
# Output helpers
# ------------------------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RED=$'\e[31m'; C_GRN=$'\e[32m'; C_YEL=$'\e[33m'; C_BLD=$'\e[1m'; C_OFF=$'\e[0m'
else
  C_RED=""; C_GRN=""; C_YEL=""; C_BLD=""; C_OFF=""
fi

STEP=0
COMPONENT="startup"
declare -A STATUS=()
STATUS_ORDER=(OS Git Docker "Docker Compose" Repository Environment "Backend container"
              "Frontend container" Caddy Firewall DNS HTTPS Frontend API)
WARNINGS=()

step() { STEP=$((STEP + 1)); COMPONENT="$2"; printf '\n%s[%d/%d] %s%s\n' "$C_BLD" "$STEP" "$TOTAL_STEPS" "$1" "$C_OFF"; }
info() { printf '      %s\n' "$*"; }
ok()   { printf '      %s✓%s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn() { printf '      %s!%s %s\n' "$C_YEL" "$C_OFF" "$*"; WARNINGS+=("$*"); }
mark() { STATUS["$1"]="$2"; }

die() {
  printf '\n%sFAILED: %s%s\n' "$C_RED" "$COMPONENT" "$C_OFF" >&2
  printf '%s\n' "$@" | sed 's/^/  /' >&2
  printf '  Full log: %s\n' "$LOG_FILE" >&2
  exit 1
}

on_error() {
  local code=$? line=${BASH_LINENO[0]} cmd=${BASH_COMMAND}
  printf '\n%sFAILED: %s%s (exit %d at line %d)\n  command: %s\n' \
    "$C_RED" "$COMPONENT" "$C_OFF" "$code" "$line" "$cmd" >&2
  if [[ -s "$LOG_FILE" ]]; then
    printf '  last log lines:\n' >&2
    tail -n 15 "$LOG_FILE" | sed 's/^/    /' >&2
  fi
  printf '  Full log: %s\n' "$LOG_FILE" >&2
  exit "$code"
}
trap on_error ERR

# Run a command with its output going to the log only; on failure show the tail.
run() {
  printf '\n$ %s\n' "$*" >>"$LOG_FILE"
  if ! "$@" >>"$LOG_FILE" 2>&1; then
    local code=$?
    printf '\n%sFAILED: %s%s\n  command: %s (exit %d)\n  last log lines:\n' "$C_RED" "$COMPONENT" "$C_OFF" "$*" "$code" >&2
    tail -n 25 "$LOG_FILE" | sed 's/^/    /' >&2
    printf '  Full log: %s\n' "$LOG_FILE" >&2
    exit "$code"
  fi
}

have() { command -v "$1" >/dev/null 2>&1; }
has_tty() { [[ -r /dev/tty && -w /dev/tty ]] && { : </dev/tty; } 2>/dev/null; }

confirm() {  # confirm "question"  -> 0 yes, 1 no (no TTY = no)
  has_tty || return 1
  local answer
  read -r -p "      $1 [y/N] " answer </dev/tty || return 1
  [[ "$answer" =~ ^[Yy]$ ]]
}

APT_UPDATED=0
apt_install() {
  local missing=()
  for pkg in "$@"; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" || missing+=("$pkg")
  done
  ((${#missing[@]})) || return 0
  if ((APT_UPDATED == 0)); then run apt-get update -q; APT_UPDATED=1; fi
  info "installing: ${missing[*]}"
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${missing[@]}"
}

# ------------------------------------------------------------------------------------
# [1] Operating system
# ------------------------------------------------------------------------------------
detect_os() {
  step "Detecting operating system..." "OS"
  [[ ${EUID} -eq 0 ]] || die "Run as root: sudo -E $0   (-E keeps GITHUB_TOKEN if you exported it)"
  mkdir -p "$(dirname "$LOG_FILE")"; : >>"$LOG_FILE"; chmod 600 "$LOG_FILE"
  printf '\n===== deploy %s =====\n' "$(date -Is)" >>"$LOG_FILE"

  [[ -r /etc/os-release ]] || die "/etc/os-release not found: unsupported system"
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-unknown}"; OS_VERSION="${VERSION_ID:-unknown}"; OS_CODENAME="${VERSION_CODENAME:-}"
  OS_LIKE="${ID_LIKE:-}"
  ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"

  case "$OS_ID" in
    ubuntu|debian) OS_FAMILY="$OS_ID" ;;
    *)
      if [[ " $OS_LIKE " == *" ubuntu "* ]]; then OS_FAMILY=ubuntu
      elif [[ " $OS_LIKE " == *" debian "* ]]; then OS_FAMILY=debian
      else die "Unsupported distribution: ${PRETTY_NAME:-$OS_ID}. Supported: Ubuntu, Debian and Debian-based systems."
      fi ;;
  esac
  # Derivatives (Mint, Pop!_OS...) use their parent's Docker repository and codename.
  if [[ "$OS_ID" != "$OS_FAMILY" ]]; then
    if [[ "$OS_FAMILY" == ubuntu ]]; then OS_CODENAME="${UBUNTU_CODENAME:-$OS_CODENAME}"; fi
    [[ -n "$OS_CODENAME" ]] || die "Cannot determine the ${OS_FAMILY} codename of ${PRETTY_NAME:-$OS_ID}"
  fi

  case "$ARCH" in
    amd64|arm64) ;;
    *) die "Unsupported CPU architecture: $ARCH (the backend image downloads Xray for amd64/arm64 only)" ;;
  esac
  have systemctl && [[ -d /run/systemd/system ]] || die "systemd is required (Docker and Caddy run as systemd services)"
  have apt-get || die "apt-get not found: unsupported system"

  if [[ "$OS_FAMILY" == ubuntu ]] && dpkg --compare-versions "${OS_VERSION}" lt 20.04 && [[ "$OS_ID" == ubuntu ]]; then
    die "Ubuntu ${OS_VERSION} is too old; use 20.04 or newer"
  fi
  if [[ "$OS_ID" == debian ]] && [[ "${OS_VERSION%%.*}" =~ ^[0-9]+$ ]] && (( ${OS_VERSION%%.*} < 11 )); then
    die "Debian ${OS_VERSION} is too old; use 11 or newer"
  fi

  ok "${PRETTY_NAME:-$OS_ID $OS_VERSION} (${OS_FAMILY}, ${OS_CODENAME:-no codename}), ${ARCH}, systemd, root"
  mark OS OK
}

# ------------------------------------------------------------------------------------
# [2] Inspect what is already on the server (read-only)
# ------------------------------------------------------------------------------------
LEGACY_UNITS=(); LEGACY_CONTAINERS=()
port_owner() {  # port_owner 8000 -> "process:pid" lines of listeners on any address
  ss -Hltnp "sport = :$1" 2>/dev/null | awk '{print $4" "$6}' || true
}

inspect_existing() {
  step "Inspecting existing infrastructure..." "Inspection"
  apt_install iproute2 >/dev/null
  info "repository dir : $([[ -d "$APP_DIR/.git" ]] && echo "$APP_DIR (git checkout)" || { [[ -e "$APP_DIR" ]] && echo "$APP_DIR (exists, not git)" || echo "$APP_DIR (absent)"; })"
  info "docker         : $(have docker && docker --version 2>/dev/null || echo 'not installed')"
  info "caddy          : $(have caddy && caddy version 2>/dev/null | awk '{print $1}' || echo 'not installed')"
  for port in 80 443 8000 8080; do
    local owners; owners="$(port_owner "$port" | tr '\n' ';')"
    info "port ${port}$(printf '%*s' $((5 - ${#port})) '') : ${owners:-free}"
  done

  # Our own stack is fine. Anything else on 80/443 blocks Caddy.
  for port in 80 443; do
    local owners; owners="$(port_owner "$port")"
    if [[ -n "$owners" && "$owners" != *'"caddy"'* ]]; then
      COMPONENT="Caddy"
      die "Port ${port} is used by another server:" "$owners" \
          "Stop it (or move its sites into Caddy) and run deploy.sh again."
    fi
  done

  # An older install (README: `docker run -p 8000:8000 skat-api` or the skat.service unit)
  # holds the ports this stack binds.
  local unit
  for unit in skat.service scat.service skat-api.service; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then LEGACY_UNITS+=("$unit"); fi
  done
  if have docker && docker info >/dev/null 2>&1; then
    while IFS='|' read -r name project ports; do
      [[ -z "$name" || "$project" == "scat" ]] && continue
      if [[ "$ports" == *":8000->"* || "$ports" == *":8080->"* ]]; then LEGACY_CONTAINERS+=("$name"); fi
    done < <(docker ps --format '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Ports}}')
    info "containers     : $(docker ps --format '{{.Names}}({{.Status}})' | tr '\n' ' ')"
    info "networks       : $(docker network ls --format '{{.Name}}' | tr '\n' ' ')"
  fi
  if ((${#LEGACY_UNITS[@]} + ${#LEGACY_CONTAINERS[@]})); then
    warn "older Скат install found: ${LEGACY_UNITS[*]} ${LEGACY_CONTAINERS[*]} (uses port 8000/8080)"
  fi
  ok "inspection done"
}

migrate_legacy() {
  ((${#LEGACY_UNITS[@]} + ${#LEGACY_CONTAINERS[@]})) || return 0
  COMPONENT="Backend container"
  info "The old install must stop so the new containers can bind 127.0.0.1:8000/8080."
  info "Nothing is deleted: units are disabled, containers are stopped with restart=no."
  if [[ "${MIGRATE_LEGACY:-0}" != 1 ]] && ! confirm "Stop ${LEGACY_UNITS[*]} ${LEGACY_CONTAINERS[*]} now?"; then
    die "Refusing to touch the running install. Re-run with MIGRATE_LEGACY=1 to stop it," \
        "or stop it yourself. To roll back later: systemctl enable --now <unit> / docker start <name>."
  fi
  local unit name
  for unit in "${LEGACY_UNITS[@]}"; do run systemctl disable --now "$unit"; ok "disabled $unit"; done
  for name in "${LEGACY_CONTAINERS[@]}"; do
    run docker update --restart=no "$name"; run docker stop "$name"; ok "stopped container $name"
  done
}

# ------------------------------------------------------------------------------------
# [3] Base packages
# ------------------------------------------------------------------------------------
install_base() {
  step "Installing base dependencies..." "Git"
  apt_install git curl ca-certificates openssl gnupg
  have git || die "git is still missing after installation"
  ok "$(git --version), $(curl --version | head -1 | awk '{print $1" "$2}'), $(openssl version | awk '{print $1" "$2}')"
  mark Git OK
}

# ------------------------------------------------------------------------------------
# [4] Docker Engine + Compose plugin
# ------------------------------------------------------------------------------------
install_docker_official() {
  local repo_os="$OS_FAMILY" key=/etc/apt/keyrings/docker.asc
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${repo_os}/gpg" -o "$key.tmp" && mv "$key.tmp" "$key" || return 1
  chmod a+r "$key"
  echo "deb [arch=${ARCH} signed-by=${key}] https://download.docker.com/linux/${repo_os} ${OS_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  APT_UPDATED=0
  apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_docker() {
  step "Installing Docker..." "Docker"
  if ! have docker; then
    info "Docker not found; installing from download.docker.com"
    if ! (install_docker_official) ; then
      warn "download.docker.com failed; falling back to the distribution packages"
      rm -f /etc/apt/sources.list.d/docker.list; APT_UPDATED=0
      apt_install docker.io
    fi
  fi
  if ! docker compose version >/dev/null 2>&1; then
    COMPONENT="Docker Compose"
    info "Compose plugin missing; installing"
    if ((APT_UPDATED == 0)); then run apt-get update -q; APT_UPDATED=1; fi
    local pkg
    for pkg in docker-compose-plugin docker-compose-v2; do
      if apt-cache show "$pkg" >/dev/null 2>&1; then apt_install "$pkg"; break; fi
    done
  fi

  if [[ -n "${DOCKER_REGISTRY_MIRROR:-}" ]]; then configure_registry_mirror; fi

  COMPONENT="Docker"
  run systemctl enable --now docker
  systemctl is-active --quiet docker || die "docker.service is not running" "$(systemctl status docker --no-pager 2>&1 | tail -5)"
  systemctl is-enabled --quiet docker || die "docker.service is not enabled on boot"
  docker info >/dev/null 2>&1 || die "Docker daemon does not answer" "$(docker info 2>&1 | tail -3)"
  ok "$(docker --version)"
  mark Docker OK

  COMPONENT="Docker Compose"
  docker compose version >/dev/null 2>&1 || die "'docker compose' is not available"
  local compose_v; compose_v="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
  # env_file with `path:`/`required:` and top-level `name:` need Compose >= 2.24.
  if dpkg --compare-versions "${compose_v:-0}" lt 2.24.0; then
    die "Docker Compose ${compose_v} is too old (need >= 2.24). Install docker-compose-plugin from download.docker.com."
  fi
  ok "Docker Compose ${compose_v}"
  mark "Docker Compose" OK
}

configure_registry_mirror() {
  have python3 || apt_install python3
  local file=/etc/docker/daemon.json
  mkdir -p /etc/docker
  if python3 - "$file" "$DOCKER_REGISTRY_MIRROR" <<'PY'
import json, os, sys
path, mirror = sys.argv[1], sys.argv[2]
data = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
mirrors = data.setdefault("registry-mirrors", [])
if mirror in mirrors:
    sys.exit(1)  # unchanged
mirrors.append(mirror)
tmp = path + ".tmp"
json.dump(data, open(tmp, "w"), indent=2)
os.replace(tmp, path)
PY
  then
    info "registry mirror added: ${DOCKER_REGISTRY_MIRROR}"
    run systemctl restart docker
  fi
}

# ------------------------------------------------------------------------------------
# [5] Repository
# ------------------------------------------------------------------------------------
ASKPASS_DIR=""
cleanup() { [[ -n "$ASKPASS_DIR" ]] && rm -rf "$ASKPASS_DIR"; unset SCAT_GIT_TOKEN; }
trap cleanup EXIT

# git with the token supplied through GIT_ASKPASS from the environment: it never appears
# in argv (ps), in .git/config, in a credential store or in the log.
git_auth() {
  if [[ -n "${SCAT_GIT_TOKEN:-}" ]]; then
    GIT_ASKPASS="$ASKPASS_DIR/askpass" GIT_TERMINAL_PROMPT=0 \
      git -c credential.helper= -c core.askPass="$ASKPASS_DIR/askpass" "$@"
  else
    GIT_TERMINAL_PROMPT=0 git -c credential.helper= "$@"
  fi
}

prepare_token() {
  ASKPASS_DIR="$(mktemp -d)"; chmod 700 "$ASKPASS_DIR"
  cat >"$ASKPASS_DIR/askpass" <<'SH'
#!/bin/sh
case "$1" in
  Username*) echo "x-access-token" ;;
  *) echo "$SCAT_GIT_TOKEN" ;;
esac
SH
  chmod 700 "$ASKPASS_DIR/askpass"

  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    export SCAT_GIT_TOKEN="$GITHUB_TOKEN"
  elif git_auth ls-remote --heads "$REPO_URL" >/dev/null 2>&1; then
    info "repository is public; no token needed"
    return 0
  elif has_tty; then
    local token=""
    read -r -s -p "      GitHub token (input hidden): " token </dev/tty; echo >/dev/tty
    [[ -n "$token" ]] || die "No token entered"
    export SCAT_GIT_TOKEN="$token"; token=""
  else
    die "The repository needs authentication. Export GITHUB_TOKEN (read-only PAT) and run: sudo -E $0"
  fi
  unset GITHUB_TOKEN
  git_auth ls-remote --heads "$REPO_URL" >/dev/null 2>&1 \
    || die "GitHub rejected the token or the repository URL (${REPO_URL}). Check the PAT has read access to it."
}

strip_credentials_from_remote() {
  local url; url="$(git -C "$APP_DIR" remote get-url origin 2>/dev/null || true)"
  if [[ "$url" =~ ^https://[^/]*@ ]]; then
    git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    warn "removed credentials that were embedded in the origin URL of $APP_DIR"
  fi
  if git -C "$APP_DIR" config --get-regexp '^http\..*extraheader' >/dev/null 2>&1; then
    git -C "$APP_DIR" config --unset-all http.https://github.com/.extraheader 2>/dev/null || true
    git -C "$APP_DIR" config --unset-all http.extraheader 2>/dev/null || true
    warn "removed stored auth headers from $APP_DIR/.git/config"
  fi
}

sync_repository() {
  step "Fetching the repository..." "Repository"
  prepare_token
  if [[ -d "$APP_DIR/.git" ]]; then
    strip_credentials_from_remote
    local current; current="$(git -C "$APP_DIR" remote get-url origin)"
    if [[ "${current%.git}" != "${REPO_URL%.git}" ]]; then
      warn "origin was ${current}; pointing it to ${REPO_URL}"
      git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    fi
    if [[ -n "$(git -C "$APP_DIR" status --porcelain --untracked-files=no)" ]]; then
      die "$APP_DIR has local modifications to tracked files:" \
          "$(git -C "$APP_DIR" status --short --untracked-files=no | head -20)" \
          "Commit/stash or revert them (git -C $APP_DIR stash), then run deploy.sh again."
    fi
    run git_auth -C "$APP_DIR" fetch --prune origin "$BRANCH"
    local before; before="$(git -C "$APP_DIR" rev-parse HEAD)"
    if [[ "$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]]; then
      run git -C "$APP_DIR" checkout "$BRANCH"
    fi
    git -C "$APP_DIR" merge --ff-only "origin/$BRANCH" >>"$LOG_FILE" 2>&1 \
      || die "Cannot fast-forward $APP_DIR to origin/$BRANCH (local commits?). Resolve it manually."
    local after; after="$(git -C "$APP_DIR" rev-parse HEAD)"
    [[ "$before" == "$after" ]] && ok "up to date at ${after:0:7}" || ok "updated ${before:0:7} -> ${after:0:7}"
  else
    if [[ -e "$APP_DIR" && -n "$(ls -A "$APP_DIR" 2>/dev/null)" ]]; then
      die "$APP_DIR exists, is not empty and is not a git checkout. Move it away or set SCAT_DIR."
    fi
    mkdir -p "$(dirname "$APP_DIR")"
    run git_auth clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    ok "cloned into $APP_DIR at $(git -C "$APP_DIR" rev-parse --short HEAD)"
  fi
  strip_credentials_from_remote
  cleanup; ASKPASS_DIR=""

  local f missing=()
  for f in Dockerfile docker-compose.yml docker-compose.frontend.yml frontend/Dockerfile frontend/nginx.conf \
           frontend/package-lock.json .env.example config.txt; do
    [[ -f "$APP_DIR/$f" ]] || missing+=("$f")
  done
  ((${#missing[@]} == 0)) || die "The checkout lacks: ${missing[*]}. Push these files to ${BRANCH} first."
  git -C "$APP_DIR" check-ignore -q .env || die ".env is not ignored by $APP_DIR/.gitignore; refusing to create secrets there"
  mark Repository OK
}

# ------------------------------------------------------------------------------------
# [6] Environment (.env)
# ------------------------------------------------------------------------------------
env_get() { grep -E "^$1=" "$APP_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
env_has() { grep -qE "^$1=" "$APP_DIR/.env" 2>/dev/null; }
env_add() { printf '%s=%s\n' "$1" "$2" >>"$APP_DIR/.env"; ADDED_KEYS+=("$1"); }

setup_env() {
  step "Configuring environment (.env)..." "Environment"
  local env_file="$APP_DIR/.env" created=0
  ADDED_KEYS=()
  if [[ ! -f "$env_file" ]]; then
    printf '# Скат production settings, created by deploy.sh on %s.\n# Secrets live here. Never commit this file. Documentation: .env.example\n' \
      "$(date -I)" >"$env_file"
    created=1
  fi
  chmod 600 "$env_file"; chown root:root "$env_file"

  # Missing keys get production values; existing values are never overwritten.
  env_has COMPOSE_PROJECT_NAME || env_add COMPOSE_PROJECT_NAME scat
  env_has COMPOSE_FILE         || env_add COMPOSE_FILE docker-compose.yml:docker-compose.frontend.yml
  env_has ALLOWED_ORIGINS      || env_add ALLOWED_ORIGINS "https://${DOMAIN},https://${WWW_DOMAIN}"
  env_has ADMIN_TOKEN          || env_add ADMIN_TOKEN "$(openssl rand -hex 32)"
  env_has TRUST_PROXY_HEADERS  || env_add TRUST_PROXY_HEADERS true
  env_has REFRESH_COOLDOWN_SECONDS || env_add REFRESH_COOLDOWN_SECONDS 600
  env_has CACHE_TTL_SECONDS    || env_add CACHE_TTL_SECONDS 86400
  env_has VITE_API_URL         || env_add VITE_API_URL "https://${API_DOMAIN}"
  env_has VITE_SITE_URL        || env_add VITE_SITE_URL "https://${DOMAIN}"
  if ! env_has VITE_DONATE_URL; then
    # Keep the donation link the project already documents (public, not a secret).
    env_add VITE_DONATE_URL "$(grep -E '^VITE_DONATE_URL=' "$APP_DIR/frontend/.env.example" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  fi

  # Validate without printing any value.
  local origins api token trust
  origins="$(env_get ALLOWED_ORIGINS)"; api="$(env_get VITE_API_URL)"
  token="$(env_get ADMIN_TOKEN)"; trust="$(env_get TRUST_PROXY_HEADERS)"
  [[ -n "$origins" ]] || die "ALLOWED_ORIGINS is empty in $env_file"
  [[ "$origins" != *"*"* ]] || die "ALLOWED_ORIGINS must list exact origins, not '*'"
  [[ ",$origins," == *",https://${DOMAIN},"* ]] || warn "ALLOWED_ORIGINS does not contain https://${DOMAIN}; the site will be blocked by CORS"
  [[ "$api" == https://* ]] || die "VITE_API_URL must start with https:// (edit $env_file)"
  [[ "${api%/}" == "https://${API_DOMAIN}" ]] || warn "VITE_API_URL is not https://${API_DOMAIN}; the frontend will call that other origin"
  if [[ -z "$token" ]]; then warn "ADMIN_TOKEN is empty: admin endpoints are disabled"
  elif ((${#token} < 32)); then warn "ADMIN_TOKEN is shorter than 32 characters; consider: openssl rand -hex 32"; fi
  [[ "${trust,,}" =~ ^(true|1|yes)$ ]] || warn "TRUST_PROXY_HEADERS is not true: behind Caddy every visitor shares one rate limit"
  grep -qiE '^(GITHUB_TOKEN|GH_TOKEN)=' "$env_file" && die "$env_file contains a GitHub token; remove it (it is only needed for git)"

  if ((created)); then ok "created $env_file (mode 600) with a generated ADMIN_TOKEN"
  elif ((${#ADDED_KEYS[@]})); then ok "kept existing values; added: ${ADDED_KEYS[*]}"
  else ok "existing $env_file is complete"; fi
  mark Environment OK
}

# ------------------------------------------------------------------------------------
# [7] Containers
# ------------------------------------------------------------------------------------
wait_healthy() {  # wait_healthy <service> <seconds>
  local service="$1" limit="$2" waited=0 id state
  id="$(cd "$APP_DIR" && docker compose ps -q "$service")"
  [[ -n "$id" ]] || return 1
  while ((waited < limit)); do
    state="$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id" 2>/dev/null || echo gone/)"
    case "$state" in
      running/healthy) return 0 ;;
      exited/*|dead/*|gone/*) return 1 ;;
    esac
    sleep 3; waited=$((waited + 3))
  done
  return 1
}

deploy_containers() {
  step "Building and starting containers..." "Backend container"
  migrate_legacy
  cd "$APP_DIR"
  run docker compose config --quiet
  info "docker compose build (first run takes a few minutes)"
  run docker compose build --pull
  run docker compose up -d --remove-orphans

  COMPONENT="Backend container"
  wait_healthy backend 120 || die "backend did not become healthy" "$(docker compose logs --tail 30 backend 2>&1)"
  curl -fsS --max-time 5 "http://${BACKEND_LOCAL}/api/health" | grep -q '"ok"' \
    || die "http://${BACKEND_LOCAL}/api/health does not answer"
  ok "backend healthy on ${BACKEND_LOCAL} (loopback only)"
  mark "Backend container" OK

  COMPONENT="Frontend container"
  wait_healthy frontend 60 || die "frontend did not become healthy" "$(docker compose logs --tail 30 frontend 2>&1)"
  curl -fsS --max-time 5 "http://${FRONTEND_LOCAL}/" | grep -q 'id="app"' \
    || die "http://${FRONTEND_LOCAL}/ does not serve the app"
  ok "frontend healthy on ${FRONTEND_LOCAL} (loopback only)"
  mark "Frontend container" OK

  # Leftover images from previous builds.
  docker image prune -f >>"$LOG_FILE" 2>&1 || true
}

# ------------------------------------------------------------------------------------
# [8] Caddy
# ------------------------------------------------------------------------------------
install_caddy() {
  have caddy && return 0
  info "installing Caddy from the official repository"
  apt_install debian-keyring debian-archive-keyring apt-transport-https
  local key=/usr/share/keyrings/caddy-stable-archive-keyring.gpg
  if curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o "$key" \
     && curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list; then
    chmod o+r "$key" /etc/apt/sources.list.d/caddy-stable.list
    APT_UPDATED=0; apt_install caddy
  else
    warn "Caddy repository unreachable; using the distribution package"
    rm -f /etc/apt/sources.list.d/caddy-stable.list; APT_UPDATED=0
    apt_install caddy
  fi
}

render_caddy_site() {
  local tls_line=""
  [[ -n "${CADDY_EMAIL:-}" ]] && tls_line="	tls ${CADDY_EMAIL}"
  cat <<EOF
# Managed by ${APP_DIR}/deploy.sh; changes here are overwritten on the next deploy.
# Certificates: Let's Encrypt, obtained and renewed automatically by Caddy.

(scat_headers) {
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		-Server
	}
}

${DOMAIN} {
${tls_line}
	import scat_headers
	encode zstd gzip
	reverse_proxy ${FRONTEND_LOCAL}
}

${WWW_DOMAIN} {
${tls_line}
	redir https://${DOMAIN}{uri} permanent
}

${API_DOMAIN} {
${tls_line}
	import scat_headers
	encode zstd gzip
	# Interactive API docs are a development aid; keep them off the public API.
	@docs path /docs /docs/* /redoc /redoc/* /openapi.json
	respond @docs 404
	reverse_proxy ${BACKEND_LOCAL} {
		# The backend trusts X-Forwarded-For (TRUST_PROXY_HEADERS): always overwrite it.
		header_up X-Forwarded-For {remote_host}
	}
}
EOF
}

configure_caddy() {
  step "Installing and configuring Caddy..." "Caddy"
  install_caddy
  have caddy || die "caddy is not installed"
  local main=/etc/caddy/Caddyfile stamp; stamp="$(date +%Y%m%d%H%M%S)"
  install -d -m 0755 "$CADDY_SITES_DIR"
  local new_site; new_site="$(mktemp --suffix=.caddy)"
  render_caddy_site >"$new_site"
  run caddy fmt --overwrite "$new_site"

  # Backups for rollback if validation fails.
  local backup_main="" backup_site=""
  [[ -f "$main" ]] && { backup_main="${main}.bak.${stamp}"; cp -p "$main" "$backup_main"; }
  [[ -f "$CADDY_SITE_FILE" ]] && { backup_site="${CADDY_SITE_FILE}.bak.${stamp}"; cp -p "$CADDY_SITE_FILE" "$backup_site"; }

  local import_line="import ${CADDY_SITES_DIR}/*.caddy"
  if [[ ! -s "$main" ]] || is_default_caddyfile "$main"; then
    printf '# Sites live in %s/. Managed by %s/deploy.sh.\n%s\n' "$CADDY_SITES_DIR" "$APP_DIR" "$import_line" >"$main"
  elif ! grep -qF "$import_line" "$main"; then
    # A custom Caddyfile: keep it, add our sites next to it, unless it already serves them.
    if grep -vE '^\s*#' "$main" | grep -qE "(^|[[:space:],])(https?://)?(www\.|api\.)?${DOMAIN//./\\.}([[:space:]:,{]|$)"; then
      if [[ "${CADDY_TAKEOVER:-0}" == 1 ]]; then
        printf '# Previous config: %s\n%s\n' "$backup_main" "$import_line" >"$main"
        warn "replaced the custom $main (backup: $backup_main)"
      else
        rm -f "$new_site"
        die "$main already defines ${DOMAIN} sites. Remove those blocks, or re-run with CADDY_TAKEOVER=1" \
            "(the current file is kept as ${backup_main})."
      fi
    else
      printf '\n%s\n' "$import_line" >>"$main"
      info "added '${import_line}' to the existing $main"
    fi
  fi

  if [[ -f "$CADDY_SITE_FILE" ]] && cmp -s "$new_site" "$CADDY_SITE_FILE"; then
    rm -f "$new_site"
  else
    install -m 0644 "$new_site" "$CADDY_SITE_FILE"; rm -f "$new_site"
  fi
  chmod 0644 "$main"  # caddy.service runs as the unprivileged caddy user

  if ! caddy validate --config "$main" --adapter caddyfile >>"$LOG_FILE" 2>&1; then
    local err; err="$(tail -n 5 "$LOG_FILE")"
    [[ -n "$backup_main" ]] && cp -p "$backup_main" "$main"
    if [[ -n "$backup_site" ]]; then cp -p "$backup_site" "$CADDY_SITE_FILE"; else rm -f "$CADDY_SITE_FILE"; fi
    die "caddy validate rejected the configuration; the previous files were restored." "$err"
  fi
  ok "caddy validate: configuration valid"
  # Drop backups identical to the current files.
  [[ -n "$backup_main" ]] && cmp -s "$backup_main" "$main" && rm -f "$backup_main"
  [[ -n "$backup_site" ]] && cmp -s "$backup_site" "$CADDY_SITE_FILE" && rm -f "$backup_site"

  run systemctl enable caddy
  if systemctl is-active --quiet caddy; then run systemctl reload caddy; else run systemctl start caddy; fi
  systemctl is-active --quiet caddy || die "caddy.service is not running" "$(journalctl -u caddy -n 20 --no-pager 2>&1)"
  systemctl is-enabled --quiet caddy || die "caddy.service is not enabled on boot"
  ok "$(caddy version | awk '{print "Caddy "$1}') running under systemd, enabled on boot"
  mark Caddy OK
}

is_default_caddyfile() {  # the placeholder the Debian/Ubuntu package ships
  grep -q '/usr/share/caddy' "$1" \
    && [[ "$(grep -vE '^\s*(#|$)' "$1" | grep -cE '^\S.*\{\s*$')" -le 1 ]] \
    && ! grep -qE '^\s*import ' "$1"
}

# ------------------------------------------------------------------------------------
# [9] Firewall
# ------------------------------------------------------------------------------------
ssh_ports() {
  local ports
  ports="$(sshd -T 2>/dev/null | awk '$1=="port"{print $2}' | sort -u | tr '\n' ' ' || true)"
  echo "${ports:-22}"
}

configure_firewall() {
  step "Checking the firewall..." "Firewall"
  local ufw_active=0 port
  have ufw && ufw status 2>/dev/null | grep -q '^Status: active' && ufw_active=1

  if ((ufw_active == 0)) && [[ "${ENABLE_FIREWALL:-0}" == 1 ]]; then
    apt_install ufw
    run ufw default deny incoming
    run ufw default allow outgoing
    for port in $(ssh_ports); do run ufw allow "${port}/tcp"; done
    run ufw --force enable
    ufw_active=1
    ok "ufw enabled"
  fi

  if ((ufw_active)); then
    for port in $(ssh_ports) 80 443; do run ufw allow "${port}/tcp"; done
    run ufw allow 443/udp   # HTTP/3
    for port in 8000 8080; do
      while ufw status numbered | grep -qE "^\[ *[0-9]+\] ${port}(/tcp)? "; do
        run ufw --force delete "$(ufw status numbered | grep -E "^\[ *[0-9]+\] ${port}(/tcp)? " | head -1 | sed -E 's/^\[ *([0-9]+)\].*/\1/')"
        warn "removed a ufw rule that opened port ${port}"
      done
    done
    ok "ufw active: allows $(ssh_ports | xargs | tr ' ' ',')/tcp (SSH), 80/tcp, 443/tcp+udp"
  else
    info "no active firewall (set ENABLE_FIREWALL=1 to let deploy.sh enable ufw for SSH, 80 and 443)"
  fi

  # Whatever the firewall says: the app ports must only listen on loopback.
  local exposed
  exposed="$(ss -Hltn 2>/dev/null | awk '{print $4}' | grep -E ':(8000|8080)$' | grep -vE '^(127\.0\.0\.1|\[::1\]):' || true)"
  [[ -z "$exposed" ]] || die "An application port listens on a public address: ${exposed//$'\n'/ }"
  ok "ports 8000/8080 listen on 127.0.0.1 only"
  mark Firewall OK
}

# ------------------------------------------------------------------------------------
# [10] DNS
# ------------------------------------------------------------------------------------
DNS_READY=1
public_ip() {  # public_ip 4|6
  curl -"$1" -fsS --max-time 6 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | awk -F= '$1=="ip"{print $2}' \
    || curl -"$1" -fsS --max-time 6 https://api64.ipify.org 2>/dev/null || true
}

check_dns() {
  step "Checking DNS..." "DNS"
  local ip4 ip6 name a aaaa
  ip4="$(public_ip 4)"; ip6="$(public_ip 6)"
  info "server public IPv4: ${ip4:-unknown}, IPv6: ${ip6:-none}"
  for name in "$DOMAIN" "$WWW_DOMAIN" "$API_DOMAIN"; do
    a="$(getent ahostsv4 "$name" 2>/dev/null | awk '{print $1}' | sort -u | xargs || true)"
    # ahostsv6 also lists IPv4-mapped ::ffff:a.b.c.d addresses; keep real AAAA records only.
    aaaa="$(getent ahostsv6 "$name" 2>/dev/null | awk '{print $1}' | grep ':' | grep -vi '^::ffff:' | sort -u | xargs || true)"
    if [[ -z "$a" && -z "$aaaa" ]]; then
      warn "DNS: ${name} does not resolve yet"; DNS_READY=0
    elif [[ -n "$ip4" && " $a " != *" $ip4 "* ]]; then
      warn "DNS: ${name} -> ${a:-no A record}, expected ${ip4}"; DNS_READY=0
    elif [[ -n "$aaaa" && " $aaaa " != *" ${ip6:-none} "* ]]; then
      warn "DNS: ${name} has AAAA ${aaaa}, which is not this server: Let's Encrypt may fail. Remove it or point it here."
      DNS_READY=0
    else
      ok "${name} -> ${a}${aaaa:+ ${aaaa}}"
    fi
  done
  if ((DNS_READY)); then mark DNS OK
  else
    mark DNS "PENDING"
    info "Point A records for ${DOMAIN}, ${WWW_DOMAIN} and ${API_DOMAIN} to ${ip4:-this server}."
    info "Caddy obtains certificates by itself once DNS propagates; just re-run deploy.sh to re-check."
    [[ "${STRICT_DNS:-0}" == 1 ]] && die "DNS is not ready (STRICT_DNS=1)"
  fi
}

# ------------------------------------------------------------------------------------
# [11] End-to-end checks through Caddy
# ------------------------------------------------------------------------------------
http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@" || true; }

check_routing_locally() {  # before DNS: Caddy answers for the host names on port 80
  local name code
  for name in "$DOMAIN" "$API_DOMAIN" "$WWW_DOMAIN"; do
    code="$(http_code --resolve "${name}:80:127.0.0.1" "http://${name}/")"
    [[ "$code" =~ ^30[178]$ ]] || die "Caddy does not route http://${name} (HTTP ${code})"
  done
  ok "Caddy routes all three host names locally (HTTPS waits for DNS)"
}

health_checks() {
  step "Running health checks..." "HTTPS"
  if ((DNS_READY == 0)); then
    COMPONENT="Caddy"; check_routing_locally
    mark HTTPS "PENDING (DNS)"; mark Frontend "PENDING (DNS)"; mark API "PENDING (DNS)"
    return 0
  fi

  # Certificates are issued on first use after a reload; allow Caddy a moment.
  local tries=0 code=""
  while ((tries < 20)); do
    code="$(http_code -I "https://${DOMAIN}")"
    [[ "$code" == 200 ]] && break
    sleep 5; tries=$((tries + 1))
  done
  [[ "$code" == 200 ]] || die "https://${DOMAIN} answered HTTP ${code:-none}" \
      "$(curl -sSI --max-time 15 "https://${DOMAIN}" 2>&1 | head -5)" \
      "$(journalctl -u caddy -n 15 --no-pager 2>&1 | grep -iE 'error|acme|certificate' | tail -5)"
  # FastAPI answers HEAD on GET routes with 405, so the API root is checked with GET.
  code="$(http_code "https://${API_DOMAIN}/")"
  [[ "$code" == 200 ]] || die "https://${API_DOMAIN} answered HTTP ${code:-none}"
  local expiry; expiry="$(echo | openssl s_client -connect "${DOMAIN}:443" -servername "${DOMAIN}" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2 || true)"
  ok "valid certificates (curl verified); ${DOMAIN} expires ${expiry:-?}, Caddy renews automatically"
  local hsts; hsts="$(curl -sI --max-time 10 "https://${DOMAIN}" | grep -ci '^strict-transport-security' || true)"
  ((hsts > 0)) || warn "no HSTS header on https://${DOMAIN}"
  code="$(http_code -I "http://${DOMAIN}/")"
  [[ "$code" =~ ^30[178]$ ]] || die "http://${DOMAIN} does not redirect to HTTPS (HTTP ${code})"
  local location; location="$(curl -sI --max-time 10 "https://${WWW_DOMAIN}/" | awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' || true)"
  [[ "$location" == "https://${DOMAIN}/" ]] || die "https://${WWW_DOMAIN} should redirect to https://${DOMAIN}/ (Location: ${location:-none})"
  ok "http -> https and ${WWW_DOMAIN} -> ${DOMAIN} redirects"
  mark HTTPS OK

  COMPONENT="Frontend"
  local page asset
  page="$(curl -fsS --max-time 15 "https://${DOMAIN}/")" || die "cannot load https://${DOMAIN}/"
  [[ "$page" == *'id="app"'* ]] || die "https://${DOMAIN}/ does not contain the app shell"
  asset="$(grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' <<<"$page" | head -1 || true)"
  [[ -n "$asset" ]] || die "no JavaScript bundle referenced by index.html"
  if ! curl -fsS --max-time 15 "https://${DOMAIN}${asset}" | grep -qF "https://${API_DOMAIN}"; then
    local other; other="$(curl -fsS --max-time 15 "https://${DOMAIN}${asset}" | grep -oE 'https://api\.[A-Za-z0-9.-]+' | sort -u | xargs || true)"
    [[ -z "$other" ]] || die "the deployed bundle calls ${other}, not https://${API_DOMAIN}. Fix VITE_API_URL in .env and rebuild."
    warn "could not find https://${API_DOMAIN} in ${asset}; check the browser network tab once"
  fi
  code="$(http_code "https://${DOMAIN}/some/deep/link")"
  [[ "$code" == 200 ]] || die "SPA fallback broken: /some/deep/link -> HTTP ${code}"
  ok "app shell, bundle ${asset} (calls https://${API_DOMAIN}), SPA fallback"
  mark Frontend OK

  COMPONENT="API"
  curl -fsS --max-time 15 "https://${API_DOMAIN}/api/health" | grep -q '"ok"' || die "GET /api/health failed"
  curl -fsS --max-time 20 "https://${API_DOMAIN}/api/scan/status" | grep -q '"cache_state"' || die "GET /api/scan/status failed"
  local results; results="$(curl -fsS --max-time 30 "https://${API_DOMAIN}/api/results")" || die "GET /api/results failed"
  grep -q '"nodes"' <<<"$results" || die "/api/results has no nodes field"
  local state; state="$(grep -oE '"cache_state":"[a-z]+"' <<<"$results" | cut -d'"' -f4 || true)"
  ok "/api/health, /api/scan/status, /api/results (cache: ${state:-?})"

  local acao
  acao="$(curl -sS -D - -o /dev/null --max-time 15 -H "Origin: https://${DOMAIN}" "https://${API_DOMAIN}/api/health" \
          | awk 'tolower($1)=="access-control-allow-origin:"{print $2}' | tr -d '\r' || true)"
  [[ "$acao" == "https://${DOMAIN}" ]] || die "CORS: origin https://${DOMAIN} is not allowed (got '${acao:-nothing}')"
  acao="$(curl -sS -D - -o /dev/null --max-time 15 -H "Origin: https://evil.example" "https://${API_DOMAIN}/api/health" \
          | awk 'tolower($1)=="access-control-allow-origin:"{print $2}' | tr -d '\r' || true)"
  [[ -z "$acao" ]] || die "CORS: a foreign origin is allowed (${acao})"
  code="$(http_code -X OPTIONS -H "Origin: https://${DOMAIN}" -H "Access-Control-Request-Method: POST" \
          "https://${API_DOMAIN}/api/scan")"
  [[ "$code" == 200 ]] || die "CORS preflight for POST /api/scan answered HTTP ${code}"
  ok "CORS allows only the site's origins"

  code="$(http_code "https://${API_DOMAIN}/api/admin/stats")"
  [[ "$code" == 401 || "$code" == 404 ]] || die "/api/admin/stats without a token answered HTTP ${code}"
  code="$(http_code -H "Authorization: Bearer wrong-token" "https://${API_DOMAIN}/api/admin/stats")"
  [[ "$code" == 401 || "$code" == 404 ]] || die "/api/admin/stats accepted a wrong token (HTTP ${code})"
  code="$(http_code "https://${API_DOMAIN}/docs")"
  [[ "$code" == 404 ]] || warn "/docs is reachable publicly (HTTP ${code})"
  ok "admin endpoints need the token; API docs are not public"
  mark API OK
}

# ------------------------------------------------------------------------------------
summary() {
  printf '\n%sSummary%s\n' "$C_BLD" "$C_OFF"
  local key value colour
  for key in "${STATUS_ORDER[@]}"; do
    value="${STATUS[$key]:-skipped}"
    case "$value" in OK) colour=$C_GRN ;; PENDING*) colour=$C_YEL ;; *) colour=$C_RED ;; esac
    printf '  %-20s %s%s%s\n' "$key" "$colour" "$value" "$C_OFF"
  done
  if ((${#WARNINGS[@]})); then
    printf '\n%sWarnings%s\n' "$C_YEL" "$C_OFF"; printf '  - %s\n' "${WARNINGS[@]}"
  fi
  printf '\n  Site: https://%s   API: https://%s\n' "$DOMAIN" "$API_DOMAIN"
  printf '  Admin token: stored in %s/.env (ADMIN_TOKEN); not printed.\n' "$APP_DIR"
  printf '  Redeploy after pushing changes: sudo -E %s/deploy.sh\n' "$APP_DIR"
}

main() {
  detect_os
  inspect_existing
  install_base
  install_docker
  sync_repository
  setup_env
  deploy_containers
  configure_caddy
  configure_firewall
  check_dns
  health_checks
  summary
}

main "$@"
