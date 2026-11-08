#!/usr/bin/env bash
# Pull the latest code from GitHub and restart the app. Run on the server:
#   bash ~/spheroid-measure/deploy/oracle/update.sh
# Sessions are kept (they live in the data folder, not in the container).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
git -C "$HERE/../.." pull --ff-only
sudo docker compose -f "$HERE/docker-compose.yml" --env-file "$HERE/.env" up -d --build
sudo docker image prune -f >/dev/null
echo "Updated. Logs: sudo docker compose -f $HERE/docker-compose.yml logs -f app"
