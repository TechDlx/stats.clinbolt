#!/usr/bin/env bash
#
# Install the current git checkout into the served locations.  Runs ON THE VM.
#
#     git -C ~/stats.clinbolt pull
#     sudo bash ~/stats.clinbolt/deploy/update.sh
#
# or, pulling first as the invoking user:
#
#     sudo bash ~/stats.clinbolt/deploy/update.sh --pull
#
# This is the counterpart to deploy/deploy.sh: use this when the VM has its own
# clone of the repository, and deploy.sh when you would rather push files from a
# workstation that has rsync.
#
# Generated dashboard data is never touched -- it is gitignored and rebuilt by
# stats-refresh.service, so publishing code can never clobber published data.
set -euo pipefail

DOMAIN="stats.clinbolt.com"
SITE_ROOT="/var/www/${DOMAIN}"
APP_ROOT="/opt/stats-clinbolt"
SERVICE_USER="statsbot"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this with sudo."
[[ -d "$REPO_ROOT/site" && -d "$REPO_ROOT/pipeline" ]] \
  || die "$REPO_ROOT does not look like the repository (no site/ and pipeline/)."
[[ -d "$APP_ROOT" ]] || die "$APP_ROOT is missing; run setup_vm.sh first."

# --------------------------------------------------------------- 1. pull
if [[ "${1:-}" == "--pull" ]]; then
  # git refuses to operate on a repo owned by another user, so pull as the
  # person who invoked sudo rather than as root.
  pull_user="${SUDO_USER:-ubuntu}"
  log "Pulling latest changes as ${pull_user}"
  sudo -u "$pull_user" git -C "$REPO_ROOT" pull --ff-only
fi

log "Installing from $REPO_ROOT"
git -C "$REPO_ROOT" log -1 --format='    commit %h  %s  (%ci)' 2>/dev/null || true

# --------------------------------------------------------------- 2. site
# --delete keeps the served tree in step with the repo, but the data
# directories are excluded so a code update never removes published data.
log "Publishing site/"
rsync -a --delete --exclude 'dashboards/*/data/' \
  "$REPO_ROOT/site/" "$SITE_ROOT/"

# --------------------------------------------------------------- 3. pipeline
log "Publishing pipeline/"
rsync -a --delete \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '*.pyc' \
  "$REPO_ROOT/pipeline/" "$APP_ROOT/pipeline/"

# --------------------------------------------------------------- 4. perms
log "Fixing ownership and permissions"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "$SITE_ROOT" "$APP_ROOT/pipeline"
find "$SITE_ROOT" -type d -exec chmod 755 {} +
find "$SITE_ROOT" -type f -exec chmod 644 {} +

# --------------------------------------------------------------- 5. units
log "Updating the refresh script and systemd units"
install -o root -g root -m 755 "$REPO_ROOT/deploy/refresh.sh" "$APP_ROOT/refresh.sh"
install -o root -g root -m 644 "$REPO_ROOT/deploy/stats-refresh.service" \
  /etc/systemd/system/stats-refresh.service
install -o root -g root -m 644 "$REPO_ROOT/deploy/stats-refresh.timer" \
  /etc/systemd/system/stats-refresh.timer
systemctl daemon-reload

# --------------------------------------------------------------- 6. deps
if [[ -f "$APP_ROOT/pipeline/requirements.txt" ]]; then
  log "Installing Python dependencies"
  sudo -u "$SERVICE_USER" "$APP_ROOT/venv/bin/pip" install -q \
    -r "$APP_ROOT/pipeline/requirements.txt"
fi

# --------------------------------------------------------------- 7. caddy
log "Updating the Caddy configuration"
install -o root -g root -m 644 "$REPO_ROOT/deploy/Caddyfile" /etc/caddy/Caddyfile
if caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
  systemctl reload caddy
  log "Caddy reloaded"
else
  warn "the Caddyfile did not validate; Caddy was left running the old config."
fi

# --------------------------------------------------------------- done
data_dir="${SITE_ROOT}/dashboards/study-size/data"
if [[ -f "${data_dir}/study_size.json" ]]; then
  log "Done. Published data is present ($(du -h "${data_dir}/study_size.json" | cut -f1))."
else
  cat <<EOF

$(printf '\033[1;33mDone, but there is no dashboard data yet.\033[0m')
Build it now (takes 10-20 minutes):

    sudo systemctl start --no-block stats-refresh.service
    journalctl -u stats-refresh.service -f
EOF
fi
