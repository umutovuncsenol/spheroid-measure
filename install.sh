#!/usr/bin/env bash
# Sferoid Measure — Mac Kurulum Scripti
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
MINIFORGE_URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-$(uname -m).sh"
CONDA_BASE="$HOME/miniforge3"

echo "======================================"
echo "  Sferoid Measure — Kurulum"
echo "======================================"
echo ""

# 1. Miniforge kontrolü
if [ ! -f "$CONDA_BASE/bin/conda" ]; then
  echo ">> Miniforge bulunamadı, indiriliyor..."
  curl -fsSL "$MINIFORGE_URL" -o /tmp/miniforge_install.sh
  bash /tmp/miniforge_install.sh -b -p "$CONDA_BASE"
  rm /tmp/miniforge_install.sh
  echo ">> Miniforge kuruldu."
else
  echo ">> Miniforge zaten kurulu."
fi

source "$CONDA_BASE/etc/profile.d/conda.sh"

# 2. Conda ortamı oluşturma
if conda env list | grep -q "^sferoid "; then
  echo ">> 'sferoid' ortamı zaten mevcut, güncelleniyor..."
  conda env update -n sferoid -f "$APP_DIR/environment.yml" --prune
else
  echo ">> 'sferoid' ortamı oluşturuluyor (bu 5-10 dakika sürebilir)..."
  conda env create -f "$APP_DIR/environment.yml"
fi

echo ">> Bağımlılıklar kuruldu."

# 3. Masaüstü launcher oluşturma
LAUNCHER="$HOME/Desktop/Sferoid Measure.command"
cat > "$LAUNCHER" <<LAUNCH
#!/usr/bin/env bash
bash "$APP_DIR/launcher/launch.sh"
LAUNCH
chmod +x "$LAUNCHER"

echo ""
echo "======================================"
echo "  Kurulum tamamlandı!"
echo ""
echo "  Uygulamayı açmak için masaüstündeki"
echo "  'Sferoid Measure' dosyasına"
echo "  çift tıklayın."
echo "======================================"
echo ""
read -p "Şimdi uygulamayı başlatmak ister misiniz? (e/h): " ANSWER
if [[ "$ANSWER" =~ ^[Ee]$ ]]; then
  conda activate sferoid
  cd "$APP_DIR"
  python -m app
fi
