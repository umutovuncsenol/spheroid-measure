#!/usr/bin/env bash
# Switch detection to the GPU model on Modal (deploy/modal/sferoid_gpu.py must be deployed first).
# Run ON THE SERVER:   bash ~/spheroid-measure/deploy/oracle/enable_modal.sh
# Undo:                bash ~/spheroid-measure/deploy/oracle/enable_modal.sh off
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HERE/.env"
[ -f "$ENV_FILE" ] || { echo "Run setup.sh first."; exit 1; }

git -C "$HERE/../.." pull --ff-only || echo "(could not update the code from GitHub; continuing)"

# drop old Modal lines
TMP="$(mktemp)"
grep -vE '^(SPHEROID_GPU|MODAL_TOKEN_ID|MODAL_TOKEN_SECRET)=' "$ENV_FILE" > "$TMP" || true

if [ "${1:-}" = "off" ]; then
  echo ">> Detection will run on this server again (slow, no GPU)."
else
  echo "Modal token. On your Mac:  cat ~/.modal.toml   (token_id and token_secret)"
  read -r -p "token_id     (starts with ak-): " TID
  read -r -s -p "token_secret (starts with as-, hidden while typing): " TSEC; echo
  case "$TID" in ak-*) ;; *) echo "token_id should start with ak-"; rm -f "$TMP"; exit 1 ;; esac
  case "$TSEC" in as-*) ;; *) echo "token_secret should start with as-"; rm -f "$TMP"; exit 1 ;; esac
  { echo "SPHEROID_GPU=modal"; echo "MODAL_TOKEN_ID=$TID"; echo "MODAL_TOKEN_SECRET=$TSEC"; } >> "$TMP"
  echo ">> Detection will run on the GPU at Modal."
fi
umask 077
cat "$TMP" > "$ENV_FILE"; rm -f "$TMP"
sudo docker compose -f "$HERE/docker-compose.yml" --env-file "$ENV_FILE" up -d --build
sudo docker image prune -f >/dev/null
echo ""
echo "Done. Watch it connect:  sudo docker compose -f $HERE/docker-compose.yml logs -f app"
echo "(you should see 'GPU model on Modal is ready.' within about a minute; Ctrl+C to stop watching)"
