#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${SIEKACZ_BUILD_VENV:-$ROOT_DIR/.venv-build-macos}"
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
"$VENV_DIR/bin/python" -m app.integrity

VERSION="$($VENV_DIR/bin/python -c 'from app.version import APP_VERSION; print(APP_VERSION)')"
MACHINE="$(uname -m)"
if [[ "$MACHINE" == "arm64" ]]; then
  PLATFORM_KEY="macos-arm64"
else
  PLATFORM_KEY="macos-x64"
fi

ICONSET="$ROOT_DIR/build/macos/AppIcon.iconset"
mkdir -p "$ICONSET"
for SIZE in 16 32 128 256 512; do
  sips -z "$SIZE" "$SIZE" assets/app_icon.png --out "$ICONSET/icon_${SIZE}x${SIZE}.png" >/dev/null
  DOUBLE=$((SIZE * 2))
  sips -z "$DOUBLE" "$DOUBLE" assets/app_icon.png --out "$ICONSET/icon_${SIZE}x${SIZE}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$ROOT_DIR/build/macos/app_icon.icns"

rm -rf dist/macos build/pyinstaller-macos
"$VENV_DIR/bin/python" -m PyInstaller \
  --noconfirm \
  --windowed \
  --name SIEKACZ9000 \
  --icon build/macos/app_icon.icns \
  --add-data "assets:assets" \
  --add-data "database/schema.sql:database" \
  --add-data "sample_data:sample_data" \
  --add-data "LICENSE:." \
  --collect-all ezdxf \
  --hidden-import app.technical_editor \
  --hidden-import app.cad_viewer \
  --hidden-import cad.inspection \
  --hidden-import import_export.dxf_io \
  --distpath dist/macos \
  --workpath build/pyinstaller-macos \
  main.py

DMG_ROOT="$ROOT_DIR/build/macos/dmg"
rm -rf "$DMG_ROOT"
mkdir -p "$DMG_ROOT"
cp -R "dist/macos/SIEKACZ9000.app" "$DMG_ROOT/SIEKACZ 9000.app"
ln -s /Applications "$DMG_ROOT/Applications"
OUTPUT="$ROOT_DIR/dist/installers/SIEKACZ9000_v${VERSION}_${PLATFORM_KEY}.dmg"
mkdir -p "$ROOT_DIR/dist/installers"
rm -f "$OUTPUT"
hdiutil create -volname "SIEKACZ 9000" -srcfolder "$DMG_ROOT" -ov -format UDZO "$OUTPUT"
echo "$OUTPUT"
