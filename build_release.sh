#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

./build_app.sh
./build_dmg.sh

echo
echo "Release artifacts:"
echo "  $PROJECT_DIR/dist/응용이미지자동화 변환기.app"
echo "  $PROJECT_DIR/release/응용이미지자동화 변환기.dmg"
