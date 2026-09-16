#!/usr/bin/env bash
#
# One-time (and safely repeatable) bootstrap for the stats.clinbolt.com VM.
#
# Target: Oracle Cloud Always Free, Ubuntu 22.04 or 24.04, either shape
#         (VM.Standard.E2.1.Micro / x86_64 or VM.Standard.A1.Flex / arm64).
#
# Run it on the VM:
#     sudo bash /home/ubuntu/stats-clinbolt-deploy/setup_vm.sh
#
# Every step checks its own state first, so re-running is harmless.
set -euo pipefail

DOMAIN="stats.clinbolt.com"
SITE_ROOT="/var/www/${DOMAIN}"
APP_ROOT="/opt/stats-clinbolt"
SERVICE_USER="statsbot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this with sudo."

# --------------------------------------------------------------- 1. platform
log "Checking platform"
ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  amd64)  log "Architecture: amd64 (x86_64 / E2.1.Micro shape)" ;;
  arm64)  log "Architecture: arm64 (Ampere A1 shape)" ;;
  *)      die "unsupported architecture '$ARCH'; expected amd64 or arm64." ;;
esac

if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  log "OS: ${PRETTY_NAME:-unknown}"
  case "${VERSION_ID:-}" in
    22.04|24.04) ;;
    *) warn "tested on Ubuntu 22.04 and 24.04; continuing on ${VERSION_ID:-unknown}." ;;
  esac
fi

# --------------------------------------------------------------- 2. packages
log "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  ca-certificates curl gnupg debian-keyring debian-archive-keyring \
  apt-transport-https git rsync python3 python3-venv python3-pip \
  iptables-persistent netfilter-persistent

# --------------------------------------------------------------- 3. caddy
if command -v caddy >/dev/null 2>&1; then
  log "Caddy already installed: $(caddy version | head -1)"
else
  log "Adding the official Caddy apt repository"
  # The Cloudsmith repo publishes both amd64 and arm64 builds.
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
  log "Installed $(caddy version | head -1)"
fi

# --------------------------------------------------------------- 4. user/dirs
if id -u "$SERVICE_USER" >/dev/null 2>&1; then
  log "Service user '$SERVICE_USER' already exists"
else
  log "Creating service user '$SERVICE_USER' (no login shell)"
  useradd --system --create-home --home-dir "$APP_ROOT" \
          --shell /usr/sbin/nologin "$SERVICE_USER"
fi

log "Creating directories"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 755 "$APP_ROOT"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 755 "$APP_ROOT/pipeline"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 755 "$APP_ROOT/cache"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 755 "$SITE_ROOT"
install -d -o root -g root -m 755 /var/log/caddy

# --------------------------------------------------------------- 5. venv
log "Creating the Python virtualenv"
if [[ ! -x "$APP_ROOT/venv/bin/python" ]]; then
  sudo -u "$SERVICE_USER" python3 -m venv "$APP_ROOT/venv"
fi
# When this script runs from a full checkout (the git-clone flow) the
# requirements file is right here; otherwise fall back to whatever a previous
# publish installed.
REQUIREMENTS=""
if [[ -f "$SCRIPT_DIR/../pipeline/requirements.txt" ]]; then
  REQUIREMENTS="$SCRIPT_DIR/../pipeline/requirements.txt"
elif [[ -f "$APP_ROOT/pipeline/requirements.txt" ]]; then
  REQUIREMENTS="$APP_ROOT/pipeline/requirements.txt"
fi

if [[ -n "$REQUIREMENTS" ]]; then
  # pip runs as the service user, which cannot read a checkout sitting in
  # /home/ubuntu (mode 750 on Ubuntu 24.04).  Stage the file somewhere readable
  # rather than loosening permissions on anyone's home directory.
  REQ_STAGED="$(mktemp /tmp/stats-requirements.XXXXXX.txt)"
  cp "$REQUIREMENTS" "$REQ_STAGED"
  chmod 644 "$REQ_STAGED"

  sudo -u "$SERVICE_USER" "$APP_ROOT/venv/bin/pip" install -q --upgrade pip
  sudo -u "$SERVICE_USER" "$APP_ROOT/venv/bin/pip" install -q -r "$REQ_STAGED"
  rm -f "$REQ_STAGED"
  log "Python dependencies installed from $REQUIREMENTS"
else
  warn "no pipeline/requirements.txt found yet -- publish the code, then re-run this script."
fi

# --------------------------------------------------------------- 6. firewall
# Oracle's Ubuntu images ship an INPUT chain that ends in a REJECT rule, so a
# plain -A ACCEPT lands after it and does nothing.  Insert above the REJECT.
open_port() {
  local port="$1"
  if iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    log "iptables: port ${port}/tcp already allowed"
    return
  fi
  local reject_line
  reject_line="$(iptables -L INPUT --line-numbers -n \
    | awk '$2 == "REJECT" || $2 == "DROP" { print $1; exit }')"
  if [[ -n "$reject_line" ]]; then
    log "iptables: allowing ${port}/tcp above the REJECT rule at line ${reject_line}"
    iptables -I INPUT "$reject_line" -p tcp --dport "$port" -j ACCEPT
  else
    log "iptables: appending ACCEPT for ${port}/tcp (no REJECT rule found)"
    iptables -A INPUT -p tcp --dport "$port" -j ACCEPT
  fi
}

log "Opening ports 80 and 443 in the instance firewall"
open_port 80
open_port 443
netfilter-persistent save >/dev/null
log "iptables rules persisted"

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  log "ufw is active; allowing 80 and 443 there too"
  ufw allow 80/tcp  >/dev/null
  ufw allow 443/tcp >/dev/null
fi

# --------------------------------------------------------------- 7. caddy cfg
log "Installing the Caddyfile"
install -o root -g root -m 644 "$SCRIPT_DIR/Caddyfile" /etc/caddy/Caddyfile
if caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
  log "Caddyfile validates"
else
  die "the Caddyfile failed validation; not restarting Caddy."
fi
systemctl enable --now caddy >/dev/null
systemctl reload caddy || systemctl restart caddy
log "Caddy is running"

# --------------------------------------------------------------- 8. systemd
log "Installing the refresh service and weekly timer"
install -o root -g root -m 755 "$SCRIPT_DIR/refresh.sh" "$APP_ROOT/refresh.sh"
install -o root -g root -m 644 "$SCRIPT_DIR/stats-refresh.service" \
  /etc/systemd/system/stats-refresh.service
install -o root -g root -m 644 "$SCRIPT_DIR/stats-refresh.timer" \
  /etc/systemd/system/stats-refresh.timer
systemctl daemon-reload
systemctl enable --now stats-refresh.timer >/dev/null
log "Timer enabled"

# --------------------------------------------------------------- done
cat <<EOF

$(printf '\033[1;32mSetup complete.\033[0m')

  Site root   : ${SITE_ROOT}
  Pipeline    : ${APP_ROOT}/pipeline
  Virtualenv  : ${APP_ROOT}/venv
  Cache       : ${APP_ROOT}/cache
  Service user: ${SERVICE_USER}

Next steps:
  1. Publish the code:
       from a checkout on this VM:  sudo bash <repo>/deploy/update.sh
       or from a workstation:       ./deploy/deploy.sh ubuntu@<VM_IP>
  2. Build the data the first time (takes 10-20 minutes):
         sudo systemctl start stats-refresh.service
         journalctl -u stats-refresh.service -f
  3. Check the site:          curl -I https://${DOMAIN}/
     Check the timer:         systemctl list-timers stats-refresh.timer

If the site is unreachable, the cause is almost always the OCI Security List
rather than this VM. See DEPLOY.md, "Troubleshooting".
EOF
