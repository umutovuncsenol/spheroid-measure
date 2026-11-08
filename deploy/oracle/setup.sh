#!/usr/bin/env bash
# One-time setup of Spheroid Review on a fresh Ubuntu server (Oracle Cloud Always Free, Ampere A1).
# Run ON THE SERVER, as the default 'ubuntu' user:
#
#   git clone https://github.com/umutovuncsenol/spheroid-measure.git
#   bash spheroid-measure/deploy/oracle/setup.sh
#
# It installs Docker, opens ports 80/443 in the server firewall, asks for the site password,
# builds the app and starts it behind HTTPS. Safe to run again (it keeps the existing password
# unless you type a new one).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HERE/.env"
DATA_DIR="${DATA_DIR:-$HOME/sferoid-data}"

echo "======================================"
echo "  Spheroid Review - server setup"
echo "======================================"

# 1. Docker
if ! command -v docker >/dev/null 2>&1; then
  echo ">> Installing Docker ..."
  sudo apt-get update -y
  sudo apt-get install -y docker.io docker-compose-v2 git curl
  sudo systemctl enable --now docker
fi
sudo usermod -aG docker "$USER" || true

# 2. Firewall inside the server. Oracle's Ubuntu images block everything except SSH with iptables,
#    even when the cloud's Security List allows the port.
echo ">> Opening ports 80 and 443 ..."
for p in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$p" -m state --state NEW -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT -p tcp --dport "$p" -m state --state NEW -j ACCEPT
  fi
done
if command -v netfilter-persistent >/dev/null 2>&1; then
  sudo netfilter-persistent save >/dev/null
else
  sudo apt-get install -y iptables-persistent && sudo netfilter-persistent save >/dev/null
fi

# 3. Settings: password and address
OLD_PW=""; OLD_SITE=""
unquote() { sed -e "s/^'//" -e "s/'$//"; }
if [ -f "$ENV_FILE" ]; then
  OLD_PW="$(grep '^SPHEROID_PASSWORD=' "$ENV_FILE" | cut -d= -f2- | unquote || true)"
  OLD_SITE="$(grep '^SITE_ADDRESS=' "$ENV_FILE" | cut -d= -f2- | unquote || true)"
fi
echo ""
while true; do
  if [ -n "$OLD_PW" ]; then
    read -r -s -p "Site password (Enter = keep the current one): " PW; echo
    PW="${PW:-$OLD_PW}"
  else
    read -r -s -p "Choose the site password the lab will use: " PW; echo
  fi
  if [ ${#PW} -lt 6 ]; then echo "Use at least 6 characters."; continue; fi
  case "$PW" in *"'"*) echo "Do not use the ' character."; continue ;; esac
  break
done

IP="$(curl -fsS https://api.ipify.org || curl -fsS https://ifconfig.me)"
DEFAULT_SITE="${OLD_SITE:-${IP//./-}.sslip.io}"
read -r -p "Site address (Enter = $DEFAULT_SITE): " SITE
SITE="${SITE:-$DEFAULT_SITE}"

mkdir -p "$DATA_DIR/sessions" "$DATA_DIR/hf-cache"
# the app runs as user 1000 inside the container; it must be able to write here
sudo chown -R 1000:1000 "$DATA_DIR"
umask 077
# single quotes: docker compose then takes the password literally (no $ expansion)
cat > "$ENV_FILE" <<EOF
SPHEROID_PASSWORD='$PW'
SITE_ADDRESS=$SITE
DATA_DIR=$DATA_DIR
OMP_NUM_THREADS=$(nproc)
EOF

# 4. Build and start (the first build downloads PyTorch etc.: 5-15 minutes)
echo ">> Building and starting the app (first time: 5-15 minutes) ..."
sudo docker compose -f "$HERE/docker-compose.yml" --env-file "$ENV_FILE" up -d --build

echo ""
echo "======================================"
echo "  Done."
echo "  Site:     https://$SITE"
echo "  Password: the one you just typed (any user name works)"
echo ""
echo "  The first page load can take a minute while the HTTPS certificate is issued,"
echo "  and the first detection downloads the model (~1 GB)."
echo "  Logs:     sudo docker compose -f $HERE/docker-compose.yml logs -f app"
echo "  Update:   bash $HERE/update.sh"
echo "  Data:     $DATA_DIR"
echo "======================================"
