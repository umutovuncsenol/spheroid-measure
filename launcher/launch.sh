#!/usr/bin/env bash
set -e
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CANDIDATES=("$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3" "/opt/homebrew/Caskroom/miniforge/base")

# Prefer the conda installation that actually has the 'sferoid' environment (a Mac can have
# several conda installs; the first one found is not necessarily the one we installed into).
CONDA_BASE=""
for c in "${CANDIDATES[@]}"; do
  if [ -d "$c/envs/sferoid" ] && [ -f "$c/etc/profile.d/conda.sh" ]; then CONDA_BASE="$c"; break; fi
done
if [ -z "$CONDA_BASE" ]; then
  for c in "${CANDIDATES[@]}"; do
    if [ -f "$c/etc/profile.d/conda.sh" ]; then CONDA_BASE="$c"; break; fi
  done
fi
if [ -z "$CONDA_BASE" ]; then
  echo "Conda bulunamadi. Once install.sh dosyasini calistirin."
  read -r -p "Kapatmak icin Enter'a basin..." _
  exit 1
fi

source "$CONDA_BASE/etc/profile.d/conda.sh"

# Otomatik güncelleme
cd "$APP_DIR"
if git rev-parse --git-dir > /dev/null 2>&1; then
  echo "Güncellemeler kontrol ediliyor..."
  git fetch origin main --quiet 2>/dev/null || true
  LOCAL=$(git rev-parse HEAD 2>/dev/null)
  REMOTE=$(git rev-parse origin/main 2>/dev/null || echo "$LOCAL")
  if [ "$LOCAL" != "$REMOTE" ]; then
    echo "Güncelleme bulundu, indiriliyor..."
    if ! git pull origin main --quiet; then
      echo "Güncelleme indirilemedi (yerel değişiklik ya da bağlantı sorunu). Mevcut sürümle devam ediliyor."
    elif git diff HEAD@{1} HEAD -- environment.yml | grep -q .; then
      echo "Yeni bağımlılıklar kuruluyor..."
      conda env update -n sferoid -f "$APP_DIR/environment.yml" --prune --quiet
    fi
    echo "Güncelleme tamamlandı."
  else
    echo "Uygulama güncel."
  fi
fi

conda activate sferoid
set +e
python -m app "$@"
CODE=$?
if [ $CODE -ne 0 ]; then
  echo ""
  echo "Uygulama bir hatayla kapandı (kod $CODE). Yukarıdaki mesajın ekran görüntüsünü alın."
  read -r -p "Kapatmak için Enter'a basın..." _
fi
exit $CODE
