#!/usr/bin/env bash
set -Eeuo pipefail

ENV_DIR="/etc/spider-bridge"
ENV_FILE="${ENV_DIR}/config.env"
BOT_DIR="/opt/spider-bridge"
BOT_FILE="${BOT_DIR}/bot.py"
APPLY_FILE="/usr/local/sbin/spider-bridge-apply"
UNINSTALL_FILE="/usr/local/sbin/spider-bridge-uninstall"
SYSTEMD_FILE="/etc/systemd/system/spider-bridge-bot.service"
REPO_RAW_URL="${REPO_RAW_URL:-${SPIDER_BRIDGE_REPO_RAW_URL:-https://raw.githubusercontent.com/kacalayar/spider/main}}"
SWAP_FILE="${SWAP_FILE:-${SPIDER_BRIDGE_SWAP_FILE:-/swapfile}}"
TMP_DIR=""

NON_INTERACTIVE=0
OPEN_UFW=1

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '\n[spider-bridge] %s\n' "$*" >&2
}

cleanup() {
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf -- "$TMP_DIR"
  fi
}

trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage:
  sudo bash install.sh [options]

Options:
  --spider-api-key VALUE       Spider.cloud API key
  --telegram-bot-token VALUE   Telegram bot token from BotFather
  --telegram-admin-ids VALUE   Comma-separated Telegram user IDs
  --proxy-user VALUE           Local proxy username, default: proxyuser
  --proxy-pass VALUE           Local proxy password, default: generated
  --port VALUE                 Local proxy port, default: 3128
  --country VALUE              Spider country code, default: US, use off for default
  --country-param VALUE        Spider country parameter: country_code or country, default: country_code
  --pool VALUE                 Spider proxy pool, default: residential
  --vps-public-ip VALUE        Public IP shown by /showproxy, default: auto-detect
  --extra-param VALUE          Optional single extra Spider password param, example: session=abc
  --repo-raw-url VALUE         Raw GitHub base URL for remote install files
  --spider-upstream-scheme VALUE  Spider upstream scheme: http or https, default: http
  --spider-upstream-host VALUE    Spider upstream host, default: proxy.spider.cloud
  --spider-upstream-port VALUE    Spider upstream port, default: 8888 for http, 8889 for https
  --swap-size-gb VALUE         Swap file size in GB, default: 2, use 0 to skip
  --swap-file VALUE            Swap file path, default: /swapfile
  --no-swap                    Do not create or enable a swap file
  --non-interactive            Fail instead of prompting for required values
  --no-open-ufw                Do not auto-open UFW port when UFW is active
  -h, --help                   Show this help

Environment variables with the same uppercase names are also supported.
EOF
}

random_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 16
  else
    tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32
    printf '\n'
  fi
}

prompt_if_empty() {
  local var_name="$1"
  local label="$2"
  local default_value="$3"
  local secret="$4"
  local required="$5"
  local current="${!var_name:-}"
  local value=""

  if [[ -n "$current" ]]; then
    return 0
  fi

  if [[ "$NON_INTERACTIVE" == "1" ]]; then
    if [[ -n "$default_value" || "$required" == "0" ]]; then
      printf -v "$var_name" '%s' "$default_value"
      return 0
    fi
    die "$var_name is required in non-interactive mode"
  fi

  local prompt="$label"
  if [[ -n "$default_value" ]]; then
    prompt="${prompt} [${default_value}]"
  fi
  prompt="${prompt}: "

  if [[ "$secret" == "1" ]]; then
    read -r -s -p "$prompt" value
    printf '\n'
  else
    read -r -p "$prompt" value
  fi

  if [[ -z "$value" ]]; then
    value="$default_value"
  fi

  if [[ "$required" == "1" && -z "$value" ]]; then
    die "$var_name is required"
  fi

  printf -v "$var_name" '%s' "$value"
}

validate_no_space() {
  local name="$1"
  local value="$2"
  [[ -n "$value" ]] || die "$name cannot be empty"
  [[ "$value" != *[[:space:]]* ]] || die "$name cannot contain whitespace"
}

validate_local_credential() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[A-Za-z0-9._-]{3,64}$ ]] || die "$name must be 3-64 chars: A-Z a-z 0-9 . _ -"
}

validate_port() {
  local value="$1"
  [[ "$value" =~ ^[0-9]+$ ]] || die "LOCAL_PROXY_PORT must be numeric"
  (( value >= 1 && value <= 65535 )) || die "LOCAL_PROXY_PORT must be between 1 and 65535"
}

validate_country() {
  local value="$1"
  [[ -z "$value" || "$value" =~ ^[A-Z]{2}$ ]] || die "SPIDER_COUNTRY_CODE must be empty or a 2-letter ISO country code"
}

validate_country_param() {
  local value="$1"
  case "$value" in
    country|country_code) ;;
    *) die "SPIDER_COUNTRY_PARAM must be country_code or country" ;;
  esac
}

validate_upstream_scheme() {
  local value="$1"
  case "$value" in
    http|https) ;;
    *) die "SPIDER_UPSTREAM_SCHEME must be http or https" ;;
  esac
}

validate_upstream_host() {
  local value="$1"
  [[ -n "$value" ]] || die "SPIDER_UPSTREAM_HOST cannot be empty"
  [[ "$value" != *[[:space:]]* ]] || die "SPIDER_UPSTREAM_HOST cannot contain whitespace"
}

validate_proxy_type() {
  local value="$1"
  case "$value" in
    residential|residential_static|residential_fast|residential_core|residential_plus|residential_premium|mobile|isp) ;;
    datacenter) SPIDER_PROXY_TYPE="isp" ;;
    *) die "Unsupported proxy pool: $value" ;;
  esac
}

validate_admin_ids() {
  local value="$1"
  [[ -z "$value" || "$value" =~ ^[0-9]+(,[0-9]+)*$ ]] || die "TELEGRAM_ADMIN_IDS must be empty or comma-separated numeric IDs"
}

validate_extra_param() {
  local value="$1"
  [[ -z "$value" || "$value" =~ ^[A-Za-z0-9._~=%:+/-]+$ ]] || die "SPIDER_EXTRA_PARAMS supports one shell-safe param only, example: session=abc"
}

validate_swap_size() {
  local value="$1"
  [[ "$value" =~ ^[0-9]+$ ]] || die "SWAP_SIZE_GB must be a whole number from 0 to 64"
  (( value >= 0 && value <= 64 )) || die "SWAP_SIZE_GB must be between 0 and 64"
}

validate_swap_file() {
  local value="$1"
  [[ -n "$value" ]] || die "SWAP_FILE cannot be empty"
  [[ "$value" == /* ]] || die "SWAP_FILE must be an absolute path"
  [[ "$value" != *[[:space:]]* ]] || die "SWAP_FILE cannot contain whitespace"
  case "$value" in
    "/"|"/dev"|"/dev/"*|"/proc"|"/proc/"*|"/sys"|"/sys/"*|"/run"|"/run/"*)
      die "Refusing unsafe SWAP_FILE path: $value"
      ;;
  esac
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --spider-api-key)
        SPIDER_API_KEY="$2"
        shift 2
        ;;
      --telegram-bot-token)
        TELEGRAM_BOT_TOKEN="$2"
        shift 2
        ;;
      --telegram-admin-ids)
        TELEGRAM_ADMIN_IDS="$2"
        shift 2
        ;;
      --proxy-user)
        LOCAL_PROXY_USER="$2"
        shift 2
        ;;
      --proxy-pass)
        LOCAL_PROXY_PASS="$2"
        shift 2
        ;;
      --port)
        LOCAL_PROXY_PORT="$2"
        shift 2
        ;;
      --country)
        SPIDER_COUNTRY_CODE="$2"
        shift 2
        ;;
      --country-param)
        SPIDER_COUNTRY_PARAM="$2"
        shift 2
        ;;
      --pool)
        SPIDER_PROXY_TYPE="$2"
        shift 2
        ;;
      --vps-public-ip)
        VPS_PUBLIC_IP="$2"
        shift 2
        ;;
      --extra-param)
        SPIDER_EXTRA_PARAMS="$2"
        shift 2
        ;;
      --repo-raw-url)
        REPO_RAW_URL="$2"
        shift 2
        ;;
      --spider-upstream-scheme)
        SPIDER_UPSTREAM_SCHEME="$2"
        shift 2
        ;;
      --spider-upstream-host)
        SPIDER_UPSTREAM_HOST="$2"
        shift 2
        ;;
      --spider-upstream-port)
        SPIDER_UPSTREAM_PORT="$2"
        shift 2
        ;;
      --swap-size-gb)
        SWAP_SIZE_GB="$2"
        shift 2
        ;;
      --swap-file)
        SWAP_FILE="$2"
        shift 2
        ;;
      --no-swap)
        SWAP_SIZE_GB=0
        shift
        ;;
      --non-interactive)
        NON_INTERACTIVE=1
        shift
        ;;
      --no-open-ufw)
        OPEN_UFW=0
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

normalize_values() {
  SPIDER_PROXY_TYPE="${SPIDER_PROXY_TYPE,,}"
  SPIDER_COUNTRY_CODE="${SPIDER_COUNTRY_CODE^^}"
  SPIDER_COUNTRY_PARAM="${SPIDER_COUNTRY_PARAM,,}"
  SPIDER_UPSTREAM_SCHEME="${SPIDER_UPSTREAM_SCHEME,,}"

  case "$SPIDER_COUNTRY_CODE" in
    OFF|DEFAULT|NONE|-) SPIDER_COUNTRY_CODE="" ;;
  esac
}

validate_values() {
  validate_no_space "SPIDER_API_KEY" "$SPIDER_API_KEY"
  validate_no_space "TELEGRAM_BOT_TOKEN" "$TELEGRAM_BOT_TOKEN"
  validate_local_credential "LOCAL_PROXY_USER" "$LOCAL_PROXY_USER"
  validate_local_credential "LOCAL_PROXY_PASS" "$LOCAL_PROXY_PASS"
  validate_port "$LOCAL_PROXY_PORT"
  validate_proxy_type "$SPIDER_PROXY_TYPE"
  validate_country "$SPIDER_COUNTRY_CODE"
  validate_country_param "$SPIDER_COUNTRY_PARAM"
  validate_upstream_scheme "$SPIDER_UPSTREAM_SCHEME"
  validate_upstream_host "$SPIDER_UPSTREAM_HOST"
  validate_port "$SPIDER_UPSTREAM_PORT"
  validate_admin_ids "$TELEGRAM_ADMIN_IDS"
  validate_extra_param "$SPIDER_EXTRA_PARAMS"
  validate_swap_size "$SWAP_SIZE_GB"
  validate_swap_file "$SWAP_FILE"
}

active_swap_exists() {
  awk 'NR > 1 {found=1} END {exit found ? 0 : 1}' /proc/swaps
}

swap_file_is_active() {
  local path="$1"
  awk -v target="$path" 'NR > 1 && $1 == target {found=1} END {exit found ? 0 : 1}' /proc/swaps
}

fstab_has_swap_file() {
  local path="$1"
  awk -v target="$path" '$1 == target && $3 == "swap" {found=1} END {exit found ? 0 : 1}' /etc/fstab 2>/dev/null
}

ensure_swap_file() {
  [[ "$SWAP_SIZE_GB" == "0" ]] && {
    log "Swap creation skipped"
    return 0
  }

  if swap_file_is_active "$SWAP_FILE"; then
    log "Swap file already active: ${SWAP_FILE}"
    return 0
  fi

  if active_swap_exists; then
    log "An active swap device/file already exists; skipping new swap creation"
    return 0
  fi

  if [[ -e "$SWAP_FILE" ]]; then
    die "${SWAP_FILE} already exists but is not active swap. Remove it manually or use --swap-file."
  fi

  local swap_dir
  swap_dir="$(dirname "$SWAP_FILE")"
  [[ -d "$swap_dir" ]] || die "Swap directory does not exist: $swap_dir"

  local required_kb available_kb
  required_kb=$((SWAP_SIZE_GB * 1024 * 1024))
  available_kb="$(df -Pk "$swap_dir" | awk 'NR == 2 {print $4}')"
  [[ "$available_kb" =~ ^[0-9]+$ ]] || die "Could not determine free disk space for $swap_dir"
  (( available_kb > required_kb + 262144 )) || die "Not enough disk space for ${SWAP_SIZE_GB}GB swap at $SWAP_FILE"

  log "Creating ${SWAP_SIZE_GB}GB swap file at ${SWAP_FILE}"
  if ! fallocate -l "${SWAP_SIZE_GB}G" "$SWAP_FILE" 2>/dev/null; then
    dd if=/dev/zero of="$SWAP_FILE" bs=1M count=$((SWAP_SIZE_GB * 1024)) status=progress
  fi

  chmod 600 "$SWAP_FILE"
  mkswap "$SWAP_FILE" >/dev/null
  swapon "$SWAP_FILE"

  if ! fstab_has_swap_file "$SWAP_FILE"; then
    printf '%s none swap sw 0 0 # spider-bridge-swap\n' "$SWAP_FILE" >>/etc/fstab
  fi

  log "Swap enabled: ${SWAP_FILE}"
}

install_packages() {
  log "Installing Ubuntu packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y squid apache2-utils curl python3 openssl ca-certificates
}

detect_public_ip() {
  if [[ -n "$VPS_PUBLIC_IP" ]]; then
    return 0
  fi

  VPS_PUBLIC_IP="$(curl -fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"
}

ensure_tmp_dir() {
  if [[ -z "$TMP_DIR" ]]; then
    TMP_DIR="$(mktemp -d)"
  fi
}

download_repo_file() {
  local repo_path="$1"
  local destination="$2"
  local base_url="${REPO_RAW_URL%/}"

  log "Downloading ${repo_path} from ${base_url}"
  curl -fsSL "${base_url}/${repo_path}" -o "$destination"
}

resolve_source_file() {
  local local_path="$1"
  local repo_path="$2"
  local output_name="$3"

  if [[ -f "$local_path" ]]; then
    printf '%s\n' "$local_path"
    return 0
  fi

  ensure_tmp_dir
  local destination="${TMP_DIR}/${output_name}"
  download_repo_file "$repo_path" "$destination"
  printf '%s\n' "$destination"
}

write_env_file() {
  install -d -m 0700 "$ENV_DIR"
  umask 077
  cat >"$ENV_FILE" <<EOF
SPIDER_API_KEY=${SPIDER_API_KEY}
SPIDER_PROXY_TYPE=${SPIDER_PROXY_TYPE}
SPIDER_COUNTRY_CODE=${SPIDER_COUNTRY_CODE}
SPIDER_COUNTRY_PARAM=${SPIDER_COUNTRY_PARAM}
SPIDER_EXTRA_PARAMS=${SPIDER_EXTRA_PARAMS}
SPIDER_UPSTREAM_SCHEME=${SPIDER_UPSTREAM_SCHEME}
SPIDER_UPSTREAM_HOST=${SPIDER_UPSTREAM_HOST}
SPIDER_UPSTREAM_PORT=${SPIDER_UPSTREAM_PORT}
LOCAL_PROXY_USER=${LOCAL_PROXY_USER}
LOCAL_PROXY_PASS=${LOCAL_PROXY_PASS}
LOCAL_PROXY_PORT=${LOCAL_PROXY_PORT}
VPS_PUBLIC_IP=${VPS_PUBLIC_IP}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_ADMIN_IDS=${TELEGRAM_ADMIN_IDS}
SETUP_TOKEN=${SETUP_TOKEN}
EOF
  chmod 0600 "$ENV_FILE"
}

install_project_files() {
  local script_dir
  local source_apply
  local source_bot
  local source_uninstall
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"

  source_apply="$(resolve_source_file "${script_dir}/files/spider-bridge-apply" "files/spider-bridge-apply" "spider-bridge-apply")"
  source_bot="$(resolve_source_file "${script_dir}/files/spider-bridge-bot.py" "files/spider-bridge-bot.py" "spider-bridge-bot.py")"
  source_uninstall="$(resolve_source_file "${script_dir}/uninstall.sh" "uninstall.sh" "spider-bridge-uninstall")"

  install -d -m 0755 "$BOT_DIR"
  install -m 0755 "$source_apply" "$APPLY_FILE"
  install -m 0755 "$source_bot" "$BOT_FILE"
  install -m 0755 "$source_uninstall" "$UNINSTALL_FILE"
}

write_systemd_service() {
  cat >"$SYSTEMD_FILE" <<EOF
[Unit]
Description=Spider Bridge Telegram Bot
After=network-online.target squid.service
Wants=network-online.target
Requires=squid.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${BOT_FILE}
Restart=always
RestartSec=5
User=root
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
}

open_ufw_port() {
  if [[ "$OPEN_UFW" != "1" ]]; then
    return 0
  fi

  if command -v ufw >/dev/null 2>&1 && ufw status | grep -qi '^Status: active'; then
    log "Opening UFW port ${LOCAL_PROXY_PORT}/tcp"
    ufw allow "${LOCAL_PROXY_PORT}/tcp" >/dev/null
  fi
}

print_summary() {
  local shown_ip="${VPS_PUBLIC_IP:-<VPS_IP>}"

  cat <<EOF

Install complete.

Local proxy:
  ${shown_ip}:${LOCAL_PROXY_PORT}:${LOCAL_PROXY_USER}:${LOCAL_PROXY_PASS}

Telegram bot:
  systemctl status spider-bridge-bot --no-pager
  journalctl -u spider-bridge-bot -f

Proxy service:
  systemctl status squid --no-pager
  /usr/local/sbin/spider-bridge-apply

Swap:
  requested ${SWAP_SIZE_GB}GB at ${SWAP_FILE}

Uninstall:
  sudo spider-bridge-uninstall
EOF

  if [[ -n "$SETUP_TOKEN" ]]; then
    cat <<EOF

No TELEGRAM_ADMIN_IDS was configured.
Open your bot on Telegram and run:
  /claim ${SETUP_TOKEN}
EOF
  fi
}

main() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Run as root: sudo bash install.sh"
  fi

  parse_args "$@"

  SPIDER_API_KEY="${SPIDER_API_KEY:-}"
  TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
  TELEGRAM_ADMIN_IDS="${TELEGRAM_ADMIN_IDS:-}"
  LOCAL_PROXY_USER="${LOCAL_PROXY_USER:-}"
  LOCAL_PROXY_PASS="${LOCAL_PROXY_PASS:-}"
  LOCAL_PROXY_PORT="${LOCAL_PROXY_PORT:-}"
  SPIDER_PROXY_TYPE="${SPIDER_PROXY_TYPE:-}"
  SPIDER_COUNTRY_CODE="${SPIDER_COUNTRY_CODE:-}"
  SPIDER_COUNTRY_PARAM="${SPIDER_COUNTRY_PARAM:-country_code}"
  SPIDER_EXTRA_PARAMS="${SPIDER_EXTRA_PARAMS:-}"
  SPIDER_UPSTREAM_SCHEME="${SPIDER_UPSTREAM_SCHEME:-http}"
  SPIDER_UPSTREAM_HOST="${SPIDER_UPSTREAM_HOST:-proxy.spider.cloud}"
  SPIDER_UPSTREAM_PORT="${SPIDER_UPSTREAM_PORT:-}"
  SWAP_SIZE_GB="${SWAP_SIZE_GB:-${SPIDER_BRIDGE_SWAP_SIZE_GB:-}}"
  VPS_PUBLIC_IP="${VPS_PUBLIC_IP:-}"

  if [[ -z "$SPIDER_UPSTREAM_PORT" ]]; then
    if [[ "${SPIDER_UPSTREAM_SCHEME,,}" == "https" ]]; then
      SPIDER_UPSTREAM_PORT="8889"
    else
      SPIDER_UPSTREAM_PORT="8888"
    fi
  fi

  local generated_pass
  generated_pass="$(random_token)"

  prompt_if_empty SPIDER_API_KEY "Spider.cloud API key" "" 1 1
  prompt_if_empty TELEGRAM_BOT_TOKEN "Telegram bot token" "" 1 1
  prompt_if_empty TELEGRAM_ADMIN_IDS "Telegram admin user IDs, comma-separated (blank enables /claim)" "" 0 0
  prompt_if_empty LOCAL_PROXY_USER "Local proxy username" "proxyuser" 0 1
  prompt_if_empty LOCAL_PROXY_PASS "Local proxy password" "$generated_pass" 0 1
  prompt_if_empty LOCAL_PROXY_PORT "Local proxy port" "3128" 0 1
  prompt_if_empty SPIDER_PROXY_TYPE "Spider proxy pool" "residential" 0 1
  prompt_if_empty SPIDER_COUNTRY_CODE "Spider country code, or off" "US" 0 0
  prompt_if_empty SWAP_SIZE_GB "Swap size in GB (0 to skip)" "2" 0 0

  normalize_values
  validate_values

  if [[ -z "$TELEGRAM_ADMIN_IDS" ]]; then
    SETUP_TOKEN="$(random_token)"
  else
    SETUP_TOKEN=""
  fi

  ensure_swap_file
  install_packages
  detect_public_ip
  install_project_files
  write_env_file
  "$APPLY_FILE"
  write_systemd_service
  systemctl daemon-reload
  systemctl enable --now spider-bridge-bot
  open_ufw_port
  print_summary
}

main "$@"
