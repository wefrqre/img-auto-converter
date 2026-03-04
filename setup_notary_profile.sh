#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [[ -f "$PROJECT_DIR/release.env" ]]; then
  source "$PROJECT_DIR/release.env"
fi

: "${APPLE_ID:?Set APPLE_ID in release.env or environment}"
: "${TEAM_ID:?Set TEAM_ID in release.env or environment}"
: "${APP_SPECIFIC_PASSWORD:?Set APP_SPECIFIC_PASSWORD in release.env or environment}"
: "${KEYCHAIN_PROFILE:?Set KEYCHAIN_PROFILE in release.env or environment}"

if ! xcrun notarytool --help >/dev/null 2>&1; then
  echo "xcrun notarytool is not available. Install Xcode command line tools first."
  exit 1
fi

xcrun notarytool store-credentials "$KEYCHAIN_PROFILE" \
  --apple-id "$APPLE_ID" \
  --team-id "$TEAM_ID" \
  --password "$APP_SPECIFIC_PASSWORD"

echo
echo "Notary profile stored:"
echo "  $KEYCHAIN_PROFILE"
