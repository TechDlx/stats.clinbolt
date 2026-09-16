#!/usr/bin/env bash
#
# Push the site and the pipeline to the VM.
#
#     ./deploy/deploy.sh ubuntu@<VM_IP>
#     ./deploy/deploy.sh ubuntu@<VM_IP> --with-data   # also push local data files
#
# rsync cannot write to /var/www or /opt directly as the ubuntu user, so files
# land in a staging directory in $HOME and are moved into place with sudo.
#
# By default the generated data files are NOT pushed: the VM rebuilds them
# itself via stats-refresh.service, which keeps the served data reproducible.
# Use --with-data to seed the site from a local build (handy for the first
# deploy, so the page is not empty while the first refresh runs).
set -euo pipefail

TARGET="${1:-}"
WITH_DATA="${2:-}"

if [[ -z "$TARGET" || "$TARGET" == "-h" || "$TARGET" == "--help" ]]; then
  cat <<'USAGE'
Usage: ./deploy/deploy.sh user@host [--with-data]

  user@host     SSH destination for the VM, e.g. ubuntu@129.153.10.20
  --with-data   also upload locally generated dashboard data
USAGE
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="stats-clinbolt-deploy"
SITE_ROOT="/var/www/stats.clinbolt.com"
APP_ROOT="/opt/stats-clinbolt"
SERVICE_USER="statsbot"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

command -v rsync >/dev/null 2>&1 || {
  echo "rsync is required on this machine." >&2; exit 1; }

log "Checking the connection to ${TARGET}"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" 'echo connected' >/dev/null

log "Preparing the staging directory on the VM"
ssh "$TARGET" "mkdir -p ~/${STAGING}/site ~/${STAGING}/pipeline ~/${STAGING}/deploy"

# --- site -------------------------------------------------------------------
DATA_FILTER=(--exclude 'dashboards/*/data/')
if [[ "$WITH_DATA" == "--with-data" ]]; then
  DATA_FILTER=()
  log "Including locally generated data files"
fi

log "Uploading site/"
rsync -az --delete "${DATA_FILTER[@]}" \
  --exclude '.DS_Store' \
  "$REPO_ROOT/site/" "$TARGET:~/${STAGING}/site/"

# --- pipeline ---------------------------------------------------------------
log "Uploading pipeline/"
rsync -az --delete \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '*.pyc' \
  "$REPO_ROOT/pipeline/" "$TARGET:~/${STAGING}/pipeline/"

# --- deploy scripts ---------------------------------------------------------
log "Uploading deploy/"
rsync -az "$REPO_ROOT/deploy/" "$TARGET:~/${STAGING}/deploy/"

# --- install ----------------------------------------------------------------
log "Installing on the VM"
# --delete is deliberately omitted for the site root so the generated data
# directories survive a deploy that did not carry data.
ssh "$TARGET" "bash -s" <<EOF
set -euo pipefail

sudo rsync -a --delete --exclude 'dashboards/*/data/' \
  ~/${STAGING}/site/ ${SITE_ROOT}/
sudo rsync -a --delete ~/${STAGING}/pipeline/ ${APP_ROOT}/pipeline/

if [ -d ~/${STAGING}/site/dashboards ]; then
  for d in ~/${STAGING}/site/dashboards/*/data; do
    [ -d "\$d" ] || continue
    name=\$(basename "\$(dirname "\$d")")
    sudo install -d -o ${SERVICE_USER} -g ${SERVICE_USER} \
      ${SITE_ROOT}/dashboards/\$name/data
    sudo rsync -a "\$d/" ${SITE_ROOT}/dashboards/\$name/data/
  done
fi

sudo chown -R ${SERVICE_USER}:${SERVICE_USER} ${SITE_ROOT} ${APP_ROOT}/pipeline
sudo find ${SITE_ROOT} -type d -exec chmod 755 {} +
sudo find ${SITE_ROOT} -type f -exec chmod 644 {} +

# Keep the refresh script and unit files in step with the repo.
sudo install -o root -g root -m 755 ~/${STAGING}/deploy/refresh.sh ${APP_ROOT}/refresh.sh
sudo install -o root -g root -m 644 ~/${STAGING}/deploy/stats-refresh.service /etc/systemd/system/stats-refresh.service
sudo install -o root -g root -m 644 ~/${STAGING}/deploy/stats-refresh.timer /etc/systemd/system/stats-refresh.timer
sudo systemctl daemon-reload

if sudo test -f ${APP_ROOT}/pipeline/requirements.txt; then
  sudo -u ${SERVICE_USER} ${APP_ROOT}/venv/bin/pip install -q \
    -r ${APP_ROOT}/pipeline/requirements.txt
fi

if ! sudo caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
  echo "WARNING: /etc/caddy/Caddyfile does not validate; not reloading Caddy." >&2
else
  sudo systemctl reload caddy
fi
EOF

log "Deployed."
cat <<EOF

  Site      : https://stats.clinbolt.com/
  Rebuild   : ssh ${TARGET} 'sudo systemctl start --no-block stats-refresh.service'
  Watch it  : ssh ${TARGET} 'journalctl -u stats-refresh.service -f'
  Timer     : ssh ${TARGET} 'systemctl list-timers stats-refresh.timer'
EOF
