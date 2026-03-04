#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

APP_PATH="$PROJECT_DIR/dist/응용이미지자동화 변환기.app"
DMG_DIR="$PROJECT_DIR/release"
DMG_PATH="$DMG_DIR/응용이미지자동화 변환기.dmg"
STAGING_DIR="$PROJECT_DIR/dist/dmg_staging"

if [[ ! -d "$APP_PATH" ]]; then
  echo "App bundle not found. Build the app first:"
  echo "  ./build_app.sh"
  exit 1
fi

rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR" "$DMG_DIR"
cp -R "$APP_PATH" "$STAGING_DIR/"

ln -s /Applications "$STAGING_DIR/Applications"

hdiutil create \
  -volname "응용이미지자동화 변환기" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

rm -rf "$STAGING_DIR"

echo
echo "DMG complete:"
echo "  $DMG_PATH"
