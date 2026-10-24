#!/usr/bin/env bash
set -e
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONDA_BASE="$HOME/miniforge3"

if [ ! -d "$CONDA_BASE" ]; then
  CONDA_BASE="$HOME/miniconda3"
fi
if [ ! -d "$CONDA_BASE" ]; then
  CONDA_BASE="/opt/homebrew/Caskroom/miniforge/base"
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
    git pull origin main --quiet
    if git diff HEAD@{1} HEAD -- environment.yml | grep -q .; then
      echo "Yeni bağımlılıklar kuruluyor..."
      conda env update -n sferoid -f "$APP_DIR/environment.yml" --prune --quiet
    fi
    echo "Güncelleme tamamlandı."
  else
    echo "Uygulama güncel."
  fi
fi

conda activate sferoid
exec python -m app "$@"
