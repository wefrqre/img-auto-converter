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
mkdir -p "$BUNDLE_BIN_DIR/vendor"

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

COLLECT_DIR="$PROJECT_DIR/dist/응용이미지자동화 변환기"
APP_BUNDLE_DIR="$PROJECT_DIR/dist/응용이미지자동화 변환기.app"
APP_CONTENTS_DIR="$APP_BUNDLE_DIR/Contents"
APP_MACOS_DIR="$APP_CONTENTS_DIR/MacOS"
APP_RESOURCES_DIR="$APP_CONTENTS_DIR/Resources"
APP_LAUNCHER="$APP_MACOS_DIR/응용이미지자동화 변환기"
APP_PLIST="$APP_CONTENTS_DIR/Info.plist"

rm -rf "$APP_BUNDLE_DIR"
mkdir -p "$APP_MACOS_DIR" "$APP_RESOURCES_DIR"

if [[ -d "$COLLECT_DIR" ]]; then
  ditto "$COLLECT_DIR" "$APP_RESOURCES_DIR"
fi

cat > "$APP_LAUNCHER" <<'EOF'
#!/bin/zsh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec "$APP_DIR/응용이미지자동화 변환기"
EOF
chmod +x "$APP_LAUNCHER"

cat > "$APP_PLIST" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>ko</string>
    <key>CFBundleDisplayName</key>
    <string>응용이미지자동화 변환기</string>
    <key>CFBundleExecutable</key>
    <string>응용이미지자동화 변환기</string>
    <key>CFBundleIdentifier</key>
    <string>com.local.applied-image-auto-converter</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>응용이미지자동화 변환기</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.0.0</string>
    <key>CFBundleVersion</key>
    <string>0.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

echo
echo "Build complete:"
echo "  $PROJECT_DIR/dist/응용이미지자동화 변환기.app"
