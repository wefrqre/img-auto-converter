#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

BUILD_PYTHON="python3"
if [[ -x "$PROJECT_DIR/.build-venv/bin/python3" ]]; then
  BUILD_PYTHON="$PROJECT_DIR/.build-venv/bin/python3"
fi

APP_VERSION="$("$BUILD_PYTHON" - <<'PY'
import re
from pathlib import Path

text = Path("app.py").read_text(encoding="utf-8")
match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
print(match.group(1) if match else "0.0.0")
PY
)"

UPDATE_INFO_URL="${UPDATE_INFO_URL:-}"
RELEASE_DOWNLOAD_URL="${RELEASE_DOWNLOAD_URL:-}"
RELEASE_NOTES="${RELEASE_NOTES:-}"

if [[ -n "$UPDATE_INFO_URL" ]]; then
  cat > "$PROJECT_DIR/update_url.txt" <<EOF
$UPDATE_INFO_URL
EOF
  echo "Updated update_url.txt:"
  echo "  $UPDATE_INFO_URL"
fi

./build_app.sh
./build_dmg.sh

if [[ -n "$UPDATE_INFO_URL" && -n "$RELEASE_DOWNLOAD_URL" ]]; then
  mkdir -p "$PROJECT_DIR/release"
  APP_VERSION="$APP_VERSION" \
  RELEASE_DOWNLOAD_URL="$RELEASE_DOWNLOAD_URL" \
  RELEASE_NOTES="$RELEASE_NOTES" \
  "$BUILD_PYTHON" - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "version": os.environ["APP_VERSION"].strip(),
    "download_url": os.environ["RELEASE_DOWNLOAD_URL"].strip(),
}
notes = os.environ.get("RELEASE_NOTES", "").strip()
if notes:
    payload["notes"] = notes

Path("release/latest.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
  echo
  echo "Update manifest generated:"
  echo "  $PROJECT_DIR/release/latest.json"
fi

echo
echo "Release artifacts:"
echo "  $PROJECT_DIR/dist/응용이미지자동화 변환기.app"
echo "  $PROJECT_DIR/release/응용이미지자동화 변환기.dmg"
if [[ -f "$PROJECT_DIR/release/latest.json" ]]; then
  echo "  $PROJECT_DIR/release/latest.json"
fi
