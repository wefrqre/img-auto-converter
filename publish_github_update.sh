#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "GH_TOKEN 이 필요합니다."
  echo "예:"
  echo "  GH_TOKEN=ghp_xxx ./publish_github_update.sh"
  exit 1
fi

REPO="${GITHUB_REPO:-wefrqre/img-auto-converter}"
BRANCH="${GITHUB_BRANCH:-main}"
ASSET_NAME="${GITHUB_ASSET_NAME:-applied-image-auto-converter.dmg}"
DMG_PATH="${DMG_PATH:-$PROJECT_DIR/release/응용이미지자동화 변환기.dmg}"
RELEASE_NOTES="${RELEASE_NOTES:-자동 배포}"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "DMG 파일이 없습니다:"
  echo "  $DMG_PATH"
  echo "먼저 ./build_dmg.sh 를 실행하세요."
  exit 1
fi

APP_VERSION="$(python3 - <<'PY'
import re
from pathlib import Path
text = Path("app.py").read_text(encoding="utf-8")
match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
print(match.group(1) if match else "0.0.0")
PY
)"

TAG="v${APP_VERSION}"
API_BASE="https://api.github.com"
AUTH_HEADER="Authorization: Bearer ${GH_TOKEN}"
ACCEPT_HEADER="Accept: application/vnd.github+json"

echo "Repo: ${REPO}"
echo "Branch: ${BRANCH}"
echo "Version: ${APP_VERSION}"
echo "Tag: ${TAG}"
echo "DMG: ${DMG_PATH}"

RELEASE_META="$(
REPO="$REPO" TAG="$TAG" GH_TOKEN="$GH_TOKEN" RELEASE_NOTES="$RELEASE_NOTES" python3 - <<'PY'
import json
import os
import urllib.error
import urllib.request
from typing import Optional

repo = os.environ["REPO"]
tag = os.environ["TAG"]
token = os.environ["GH_TOKEN"]
notes = os.environ["RELEASE_NOTES"]

api = "https://api.github.com"
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "applied-image-auto-converter-release-script",
}

def request(url: str, method: str = "GET", payload: Optional[dict] = None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))

release = None
try:
    release = request(f"{api}/repos/{repo}/releases/tags/{tag}")
except urllib.error.HTTPError as e:
    if e.code != 404:
        raise

if release is None:
    release = request(
        f"{api}/repos/{repo}/releases",
        method="POST",
        payload={
            "tag_name": tag,
            "name": tag,
            "body": notes,
            "draft": False,
            "prerelease": False,
            "make_latest": "true",
        },
    )

print(json.dumps({
    "id": release["id"],
    "upload_url": release["upload_url"].split("{", 1)[0],
    "html_url": release["html_url"],
    "assets": release.get("assets", []),
}, ensure_ascii=False))
PY
)"

RELEASE_ID="$(echo "$RELEASE_META" | python3 -c 'import sys, json; print(json.load(sys.stdin)["id"])')"
UPLOAD_URL="$(echo "$RELEASE_META" | python3 -c 'import sys, json; print(json.load(sys.stdin)["upload_url"])')"
RELEASE_URL="$(echo "$RELEASE_META" | python3 -c 'import sys, json; print(json.load(sys.stdin)["html_url"])')"

ASSET_ID_TO_DELETE="$(
RELEASE_META="$RELEASE_META" ASSET_NAME="$ASSET_NAME" python3 - <<'PY'
import json
import os

meta = json.loads(os.environ["RELEASE_META"])
name = os.environ["ASSET_NAME"]
for asset in meta.get("assets", []):
    if asset.get("name") == name:
        print(asset.get("id"))
        break
PY
)"

if [[ -n "${ASSET_ID_TO_DELETE:-}" ]]; then
  echo "기존 에셋 삭제: ${ASSET_NAME} (id=${ASSET_ID_TO_DELETE})"
  curl -sS -X DELETE \
    -H "$AUTH_HEADER" \
    -H "$ACCEPT_HEADER" \
    "${API_BASE}/repos/${REPO}/releases/assets/${ASSET_ID_TO_DELETE}" >/dev/null
fi

echo "에셋 업로드 중..."
UPLOAD_RESP="$(
curl -sS --fail \
  -X POST \
  -H "$AUTH_HEADER" \
  -H "$ACCEPT_HEADER" \
  -H "Content-Type: application/octet-stream" \
  --data-binary "@${DMG_PATH}" \
  "${UPLOAD_URL}?name=${ASSET_NAME}"
)"

DOWNLOAD_URL="$(echo "$UPLOAD_RESP" | python3 -c 'import sys, json; print(json.load(sys.stdin)["browser_download_url"])')"

LATEST_JSON_CONTENT="$(
DOWNLOAD_URL="$DOWNLOAD_URL" APP_VERSION="$APP_VERSION" RELEASE_NOTES="$RELEASE_NOTES" python3 - <<'PY'
import json
import os
payload = {
    "version": os.environ["APP_VERSION"],
    "download_url": os.environ["DOWNLOAD_URL"],
}
notes = os.environ.get("RELEASE_NOTES", "").strip()
if notes:
    payload["notes"] = notes
print(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
PY
)"

LATEST_JSON_PATH="$PROJECT_DIR/release/latest.json"
printf "%s" "$LATEST_JSON_CONTENT" > "$LATEST_JSON_PATH"

REPO="$REPO" BRANCH="$BRANCH" GH_TOKEN="$GH_TOKEN" LATEST_JSON_CONTENT="$LATEST_JSON_CONTENT" python3 - <<'PY'
import base64
import json
import os
import urllib.error
import urllib.request
from typing import Optional

repo = os.environ["REPO"]
branch = os.environ["BRANCH"]
token = os.environ["GH_TOKEN"]
content = os.environ["LATEST_JSON_CONTENT"]
path = "latest.json"
api = "https://api.github.com"

headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "applied-image-auto-converter-release-script",
}

def req(url: str, method: str = "GET", payload: Optional[dict] = None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))

sha = None
url = f"{api}/repos/{repo}/contents/{path}?ref={branch}"
try:
    current = req(url)
    sha = current.get("sha")
except urllib.error.HTTPError as e:
    if e.code != 404:
        raise

payload = {
    "message": "chore: update latest.json",
    "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    "branch": branch,
}
if sha:
    payload["sha"] = sha

req(f"{api}/repos/{repo}/contents/{path}", method="PUT", payload=payload)
PY

echo
echo "완료:"
echo "Release: ${RELEASE_URL}"
echo "Asset: ${DOWNLOAD_URL}"
echo "latest.json(raw): https://raw.githubusercontent.com/${REPO}/${BRANCH}/latest.json"
echo "local latest.json: ${LATEST_JSON_PATH}"
