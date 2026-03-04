#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

export PYINSTALLER_CONFIG_DIR="$PROJECT_DIR/.pyinstaller"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

BUNDLE_BIN_DIR="$PROJECT_DIR/bundle_bin"

BUILD_PYTHON="python3"

if [[ -x "$PROJECT_DIR/.build-venv/bin/python3" ]]; then
  if "$PROJECT_DIR/.build-venv/bin/python3" -c "import PyInstaller, PySide6" >/dev/null 2>&1; then
    BUILD_PYTHON="$PROJECT_DIR/.build-venv/bin/python3"
  fi
fi

if ! "$BUILD_PYTHON" -c "import PyInstaller, PySide6" >/dev/null 2>&1; then
  python3 -m venv "$PROJECT_DIR/.build-venv"
  PIP_CACHE_DIR=/tmp/pip-cache "$PROJECT_DIR/.build-venv/bin/pip" install pyinstaller PySide6
  BUILD_PYTHON="$PROJECT_DIR/.build-venv/bin/python3"
fi

mkdir -p "$BUNDLE_BIN_DIR"
rm -rf "$BUNDLE_BIN_DIR/vendor"

INKSCAPE_SOURCE_BIN=""
if command -v inkscape >/dev/null 2>&1; then
  INKSCAPE_WRAPPER="$(command -v inkscape)"
  if file "$INKSCAPE_WRAPPER" | grep -q "shell script"; then
    INKSCAPE_SOURCE_BIN="$(awk -F"'" '/^exec / { print $2; exit }' "$INKSCAPE_WRAPPER")"
  else
    INKSCAPE_SOURCE_BIN="$INKSCAPE_WRAPPER"
  fi
fi

if [[ -n "$INKSCAPE_SOURCE_BIN" && -x "$INKSCAPE_SOURCE_BIN" ]]; then
  INKSCAPE_APP_DIR="$(cd "$(dirname "$INKSCAPE_SOURCE_BIN")/../.." && pwd)"
else
  echo "Warning: Inkscape bundle source was not found. The app will fall back to a system install."
fi

"$BUILD_PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  app.spec

if [[ -n "${INKSCAPE_APP_DIR:-}" ]]; then
  APP_VENDOR_DIR="$PROJECT_DIR/dist/응용이미지자동화 변환기.app/Contents/Resources/vendor"
  mkdir -p "$APP_VENDOR_DIR"
  rm -rf "$APP_VENDOR_DIR/Inkscape.app"
  ditto "$INKSCAPE_APP_DIR" "$APP_VENDOR_DIR/Inkscape.app"
  echo "Bundled Inkscape into app:"
  echo "  $APP_VENDOR_DIR/Inkscape.app"
fi

echo
echo "Build complete:"
echo "  $PROJECT_DIR/dist/응용이미지자동화 변환기.app"
