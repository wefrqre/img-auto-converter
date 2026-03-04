#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [[ -f "$PROJECT_DIR/release.env" ]]; then
  source "$PROJECT_DIR/release.env"
fi

APP_PATH="${APP_PATH:-dist/응용이미지자동화 변환기.app}"
DMG_PATH="${DMG_PATH:-release/응용이미지자동화 변환기.dmg}"

: "${DEVELOPER_ID_APPLICATION:?Set DEVELOPER_ID_APPLICATION in release.env or environment}"
: "${KEYCHAIN_PROFILE:?Set KEYCHAIN_PROFILE in release.env or environment}"

if [[ ! -d "$APP_PATH" ]]; then
  echo "App bundle not found:"
  echo "  $APP_PATH"
  echo "Build it first with ./build_release.sh or ./build_app.sh"
  exit 1
fi

if ! xcrun notarytool --help >/dev/null 2>&1; then
  echo "xcrun notarytool is not available. Install Xcode command line tools first."
  exit 1
fi

if ! security find-identity -v -p codesigning | grep -F "$DEVELOPER_ID_APPLICATION" >/dev/null; then
  echo "Signing identity not found in keychain:"
  echo "  $DEVELOPER_ID_APPLICATION"
  echo "Install the Developer ID Application certificate, then try again."
  exit 1
fi

echo "Signing app:"
echo "  $APP_PATH"

codesign \
  --force \
  --deep \
  --options runtime \
  --timestamp \
  --sign "$DEVELOPER_ID_APPLICATION" \
  "$APP_PATH"

codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo
echo "Rebuilding DMG from signed app..."
./build_dmg.sh

if [[ ! -f "$DMG_PATH" ]]; then
  echo "DMG not found after rebuild:"
  echo "  $DMG_PATH"
  exit 1
fi

echo
echo "Submitting for notarization:"
echo "  $DMG_PATH"

xcrun notarytool submit "$DMG_PATH" \
  --keychain-profile "$KEYCHAIN_PROFILE" \
  --wait

echo
echo "Stapling notarization ticket..."
xcrun stapler staple "$APP_PATH"
xcrun stapler staple "$DMG_PATH"

echo
echo "Validating stapled artifacts..."
xcrun stapler validate "$APP_PATH"
xcrun stapler validate "$DMG_PATH"

echo
echo "Notarized release ready:"
echo "  $APP_PATH"
echo "  $DMG_PATH"
