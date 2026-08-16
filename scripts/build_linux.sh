#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${SIEKACZ_BUILD_VENV:-$ROOT_DIR/.venv-build-linux}"
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
"$VENV_DIR/bin/python" -m app.integrity

VERSION="$($VENV_DIR/bin/python -c 'from app.version import APP_VERSION; print(APP_VERSION)')"
MACHINE="$(uname -m)"
if [[ "$MACHINE" == "aarch64" || "$MACHINE" == "arm64" ]]; then
  PLATFORM_KEY="linux-aarch64"
  APPIMAGE_ARCH="aarch64"
else
  PLATFORM_KEY="linux-x86_64"
  APPIMAGE_ARCH="x86_64"
fi

rm -rf dist/linux build/pyinstaller-linux build/SIEKACZ9000.AppDir
"$VENV_DIR/bin/python" -m PyInstaller \
  --noconfirm \
  --windowed \
  --name SIEKACZ9000 \
  --icon assets/app_icon.png \
  --add-data "assets:assets" \
  --add-data "database/schema.sql:database" \
  --add-data "sample_data:sample_data" \
  --add-data "LICENSE:." \
  --collect-all ezdxf \
  --hidden-import app.technical_editor \
  --hidden-import app.cad_viewer \
  --hidden-import cad.inspection \
  --hidden-import import_export.dxf_io \
  --distpath dist/linux \
  --workpath build/pyinstaller-linux \
  main.py

APPDIR="$ROOT_DIR/build/SIEKACZ9000.AppDir"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/512x512/apps"
cp -R dist/linux/SIEKACZ9000/. "$APPDIR/usr/bin/"
cp assets/app_icon.png "$APPDIR/siekacz9000.png"
cp assets/app_icon.png "$APPDIR/usr/share/icons/hicolor/512x512/apps/siekacz9000.png"
cp installer_src/siekacz9000.desktop "$APPDIR/siekacz9000.desktop"
cp installer_src/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun" "$APPDIR/usr/bin/SIEKACZ9000"

TOOL="$ROOT_DIR/build/appimagetool-${APPIMAGE_ARCH}.AppImage"
curl --fail --location --retry 3 \
  "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${APPIMAGE_ARCH}.AppImage" \
  --output "$TOOL"
chmod +x "$TOOL"
OUTPUT="$ROOT_DIR/dist/installers/SIEKACZ9000_v${VERSION}_${PLATFORM_KEY}.AppImage"
mkdir -p "$ROOT_DIR/dist/installers"
ARCH="$APPIMAGE_ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$OUTPUT"
chmod +x "$OUTPUT"
echo "$OUTPUT"
