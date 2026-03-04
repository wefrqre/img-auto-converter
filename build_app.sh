#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

export PYINSTALLER_CONFIG_DIR="$PROJECT_DIR/.pyinstaller"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

BUILD_PYTHON="python3"

if [[ -x "$PROJECT_DIR/.build-venv/bin/python3" ]]; then
  if "$PROJECT_DIR/.build-venv/bin/python3" -c "import PyInstaller" >/dev/null 2>&1; then
    BUILD_PYTHON="$PROJECT_DIR/.build-venv/bin/python3"
  fi
fi

if ! "$BUILD_PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
  python3 -m venv "$PROJECT_DIR/.build-venv"
  PIP_CACHE_DIR=/tmp/pip-cache "$PROJECT_DIR/.build-venv/bin/pip" install pyinstaller
  BUILD_PYTHON="$PROJECT_DIR/.build-venv/bin/python3"
fi

"$BUILD_PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  app.spec

echo
echo "Build complete:"
echo "  $PROJECT_DIR/dist/응용이미지자동화 변환기.app"
