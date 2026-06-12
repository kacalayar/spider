#!/usr/bin/env bash
set -Eeuo pipefail

ENV_DIR="/etc/spider-bridge"
STATE_DIR="/var/lib/spider-bridge"
BOT_DIR="/opt/spider-bridge"
APPLY_FILE="/usr/local/sbin/spider-bridge-apply"
UNINSTALL_FILE="/usr/local/sbin/spider-bridge-uninstall"
SYSTEMD_FILE="/etc/systemd/system/spider-bridge-bot.service"
SQUID_CONF="/etc/squid/squid.conf"
SQUID_USERS="/etc/squid/spider_bridge_users"

ASSUME_YES=0
DRY_RUN=0
KEEP_CONFIG=0
KEEP_STATE=0
RESTORE_SQUID=1
PURGE_PACKAGES=0
STOP_SQUID=1

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[spider-bridge uninstall] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage:
  sudo bash uninstall.sh [options]

Options:
  -y, --yes              Do not ask for confirmation
  --dry-run              Print actions without changing the system
  --keep-config          Keep /etc/spider-bridge
  --keep-state           Keep /var/lib/spider-bridge
  --no-restore-squid     Do not restore pre-install Squid backup
  --no-stop-squid        Do not stop/disable Squid even when config is managed
  --purge-packages       Apt purge squid and apache2-utils after removing bridge
  -h, --help             Show this help

Default behavior:
  - Stop and disable spider-bridge-bot.
  - Remove /etc/systemd/system/spider-bridge-bot.service.
  - Remove /opt/spider-bridge and spider-bridge helper commands.
  - Remove /etc/spider-bridge and /var/lib/spider-bridge unless kept.
  - Remove /etc/squid/spider_bridge_users.
  - If /etc/squid/squid.conf is managed by spider-bridge, save it, then restore
    the newest /etc/squid/squid.conf.pre-spider-bridge.* backup when available.
  - Packages are not removed unless --purge-packages is used.
EOF
}

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY-RUN:'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

remove_path() {
  local path="$1"
  case "$path" in
    ""|"/"|"/etc"|"/opt"|"/usr"|"/usr/local"|"/usr/local/sbin"|"/var"|"/var/lib"|"/etc/squid")
      die "Refusing to remove unsafe path: ${path:-<empty>}"
      ;;
  esac
  [[ -e "$path" || -L "$path" ]] || return 0
  run rm -rf -- "$path"
}

systemctl_exists() {
  command -v systemctl >/dev/null 2>&1
}

service_known() {
  local unit="$1"
  systemctl_exists || return 1
  systemctl list-unit-files "$unit" >/dev/null 2>&1 || [[ -f "$SYSTEMD_FILE" ]]
}

stop_disable_service() {
  local unit="$1"
  service_known "$unit" || return 0

  log "Stopping and disabling ${unit}"
  run systemctl stop "$unit" || true
  run systemctl disable "$unit" || true
}

daemon_reload() {
  systemctl_exists || return 0
  log "Reloading systemd"
  run systemctl daemon-reload
  run systemctl reset-failed || true
}

squid_conf_is_managed() {
  [[ -f "$SQUID_CONF" ]] || return 1
  grep -q "Managed by spider-bridge" "$SQUID_CONF"
}

newest_squid_backup() {
  local backup=""
  backup="$(find /etc/squid -maxdepth 1 -type f -name 'squid.conf.pre-spider-bridge.*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk 'NR==1 {print $2}')"
  [[ -n "$backup" ]] || return 1
  printf '%s\n' "$backup"
}

restore_or_stop_squid() {
  squid_conf_is_managed || {
    log "Squid config is not managed by spider-bridge; leaving Squid untouched"
    return 0
  }

  local removed_copy="${SQUID_CONF}.spider-bridge-removed.$(date +%Y%m%d%H%M%S)"
  log "Saving current managed Squid config to ${removed_copy}"
  run cp "$SQUID_CONF" "$removed_copy"

  if [[ "$RESTORE_SQUID" == "1" ]]; then
    local backup=""
    if backup="$(newest_squid_backup)"; then
      log "Restoring Squid config backup: ${backup}"
      run cp "$backup" "$SQUID_CONF"

      if systemctl_exists; then
        log "Restarting Squid with restored config"
        run systemctl restart squid || log "Squid restart failed; check systemctl status squid"
      fi
      return 0
    fi
    log "No pre-spider-bridge Squid backup found"
  fi

  if [[ "$STOP_SQUID" == "1" ]] && systemctl_exists; then
    log "Stopping and disabling Squid because bridge config was installed"
    run systemctl stop squid || true
    run systemctl disable squid || true
  fi
}

purge_packages() {
  [[ "$PURGE_PACKAGES" == "1" ]] || return 0

  if ! command -v apt-get >/dev/null 2>&1; then
    log "apt-get not found; skipping package purge"
    return 0
  fi

  log "Purging bridge packages: squid apache2-utils"
  export DEBIAN_FRONTEND=noninteractive
  run apt-get purge -y squid apache2-utils
  run apt-get autoremove -y
}

confirm() {
  [[ "$ASSUME_YES" == "1" || "$DRY_RUN" == "1" ]] && return 0

  cat <<EOF
This will uninstall Spider Bridge from this VPS.

It may stop Squid if the current Squid config is managed by spider-bridge.
It will not remove apt packages unless --purge-packages is used.

Continue? [y/N]:
EOF

  local answer=""
  read -r answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) die "Cancelled" ;;
  esac
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -y|--yes)
        ASSUME_YES=1
        shift
        ;;
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      --keep-config)
        KEEP_CONFIG=1
        shift
        ;;
      --keep-state)
        KEEP_STATE=1
        shift
        ;;
      --no-restore-squid)
        RESTORE_SQUID=0
        shift
        ;;
      --no-stop-squid)
        STOP_SQUID=0
        shift
        ;;
      --purge-packages)
        PURGE_PACKAGES=1
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

main() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Run as root: sudo bash uninstall.sh"
  fi

  parse_args "$@"
  confirm

  stop_disable_service spider-bridge-bot.service

  log "Removing systemd unit and bridge files"
  remove_path "$SYSTEMD_FILE"
  remove_path "$BOT_DIR"
  remove_path "$APPLY_FILE"
  remove_path "$UNINSTALL_FILE"
  remove_path "$SQUID_USERS"

  if [[ "$KEEP_CONFIG" == "0" ]]; then
    remove_path "$ENV_DIR"
  else
    log "Keeping ${ENV_DIR}"
  fi

  if [[ "$KEEP_STATE" == "0" ]]; then
    remove_path "$STATE_DIR"
  else
    log "Keeping ${STATE_DIR}"
  fi

  restore_or_stop_squid
  daemon_reload
  purge_packages

  log "Uninstall complete"
}

main "$@"
