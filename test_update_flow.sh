#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_BIN="$ROOT_DIR/dist/응용이미지자동화 변환기.app/Contents/MacOS/응용이미지자동화 변환기"
TEST_JSON="$ROOT_DIR/release/latest.update-test.json"

if [[ ! -x "$APP_BIN" ]]; then
  echo "앱 실행 파일을 찾지 못했습니다:"
  echo "  $APP_BIN"
  echo "먼저 ./build_app.sh 를 실행하세요."
  exit 1
fi

cat > "$TEST_JSON" <<'JSON'
{
  "version": "9.9.9",
  "download_url": "https://example.com/update-test.dmg",
  "notes": "업데이트 테스트 팝업 확인용"
}
JSON

echo "업데이트 테스트 JSON 생성:"
echo "  $TEST_JSON"
echo
echo "앱을 업데이트 테스트 모드로 실행합니다."
echo "- 팝업 제목: 응용 이미지 자동 변환기"
echo "- 기대 동작: '새 버전 9.9.9이 있습니다' 팝업 표시"
echo "- 종료: 앱을 닫으면 테스트 종료"
echo

APP_UPDATE_INFO_URL="file://$TEST_JSON" "$APP_BIN"
