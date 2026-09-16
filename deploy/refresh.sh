#!/usr/bin/env bash
#
# Rebuild every dashboard's data and publish it atomically.
#
# Run by stats-refresh.service (weekly via stats-refresh.timer), or by hand:
#     sudo systemctl start stats-refresh.service
#
# The build writes into a temp directory.  Nothing reaches the served site
# until the JSON has been validated, so a failed fetch or a malformed build
# leaves the previous week's data in place.
set -euo pipefail

DOMAIN="stats.clinbolt.com"
SITE_ROOT="/var/www/${DOMAIN}"
APP_ROOT="/opt/stats-clinbolt"
PIPELINE="${APP_ROOT}/pipeline"
VENV_PYTHON="${APP_ROOT}/venv/bin/python"
CACHE_DIR="${APP_ROOT}/cache"
SERVICE_USER="statsbot"

# Dashboards to rebuild: "<pipeline module dir>:<site dashboard dir>"
DASHBOARDS=(
  "study_size:study-size"
)

log()  { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { printf '[%s] ERROR: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

[[ -x "$VENV_PYTHON" ]] || { fail "virtualenv missing at ${VENV_PYTHON}; run setup_vm.sh."; exit 1; }

TEMP_ROOT="$(mktemp -d /tmp/stats-refresh.XXXXXX)"
cleanup() { rm -rf "$TEMP_ROOT"; }
trap cleanup EXIT

overall_status=0

for entry in "${DASHBOARDS[@]}"; do
  module="${entry%%:*}"
  site_dir="${entry##*:}"
  build_script="${PIPELINE}/${module}/build.py"
  staging="${TEMP_ROOT}/${site_dir}"
  target="${SITE_ROOT}/dashboards/${site_dir}/data"

  log "=== ${site_dir} ==="

  if [[ ! -f "$build_script" ]]; then
    fail "${site_dir}: no build script at ${build_script}; skipping."
    overall_status=1
    continue
  fi

  mkdir -p "$staging"

  log "${site_dir}: building"
  if ! "$VENV_PYTHON" "$build_script" --out "$staging" --cache-dir "$CACHE_DIR"; then
    fail "${site_dir}: build failed; keeping the existing published data."
    overall_status=1
    continue
  fi

  # Validate every JSON file the build produced before publishing any of it.
  validated=1
  shopt -s nullglob
  produced=("$staging"/*.json)
  shopt -u nullglob

  if [[ ${#produced[@]} -eq 0 ]]; then
    fail "${site_dir}: build produced no JSON; keeping the existing data."
    overall_status=1
    continue
  fi

  for file in "${produced[@]}"; do
    # meta.json is a tiny sidecar, not a dashboard payload.
    if [[ "$(basename "$file")" == "meta.json" ]]; then
      if ! "$VENV_PYTHON" -c "import json,sys; json.load(open(sys.argv[1]))" "$file"; then
        fail "${site_dir}: $(basename "$file") is not valid JSON."
        validated=0
      fi
      continue
    fi
    if ! (cd "$PIPELINE" && "$VENV_PYTHON" -m common.validate "$file"); then
      validated=0
    fi
  done

  if [[ $validated -ne 1 ]]; then
    fail "${site_dir}: validation failed; keeping the existing published data."
    overall_status=1
    continue
  fi

  # Publish: write the new files beside the live ones, then swap the directory
  # in a single rename so a reader never sees a half-written set.
  log "${site_dir}: publishing"
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 755 "$(dirname "$target")"

  incoming="${target}.incoming.$$"
  previous="${target}.previous.$$"

  rm -rf "$incoming"
  cp -a "$staging" "$incoming"
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "$incoming"
  chmod 755 "$incoming"
  find "$incoming" -type f -exec chmod 644 {} +

  if [[ -d "$target" ]]; then
    mv "$target" "$previous"
  fi
  mv "$incoming" "$target"
  rm -rf "$previous"

  size="$(du -h "$target" | tail -1 | cut -f1)"
  log "${site_dir}: published (${size})"
done

if [[ $overall_status -eq 0 ]]; then
  log "Refresh complete; all dashboards updated."
else
  fail "Refresh finished with errors; some dashboards kept their previous data."
fi

exit "$overall_status"
