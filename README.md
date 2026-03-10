# 응용 이미지 자동 변환기

macOS용 SVG 자동 변환 앱입니다.  
Figma에서 내보낸 SVG 파일을 `Desktop/figma_exports/svg` 폴더에 저장하면 PNG가 자동으로 생성됩니다.

## 주요 기능

- 앱 실행 시 작업 폴더 자동 생성
  - `~/Desktop/figma_exports/svg`
  - `~/Desktop/figma_exports/png_96dpi`
  - `~/Desktop/figma_exports/png_192dpi`
- 앱 실행 직후 자동 감시 시작
- SVG 저장 시 PNG 자동 변환
- DPI 96 / 192 전환 지원
- 변환 내역 및 파일 정보 확인 UI 제공
- 앱 버전 표시
- 실행 시 자동 업데이트 체크

## 변환 규격

- 포맷: PNG
- DPI: `96` 또는 `192`
- 색상 타입: `TrueColorAlpha (RGBA)`
- Indexed PNG 방지
- 투명 배경 유지

변환 파이프라인:

1. `Inkscape`로 SVG를 PNG로 렌더링
2. 후처리 단계에서 DPI 및 PNG 타입 보정

## 사용 방법

1. 앱 실행
2. `Desktop/figma_exports/svg` 폴더에 SVG 저장
3. 결과 PNG 확인

출력 폴더:

- 96 DPI 선택 시: `Desktop/figma_exports/png_96dpi`
- 192 DPI 선택 시: `Desktop/figma_exports/png_192dpi`

## 첫 실행 안내

앱을 처음 실행하면 작업 폴더를 자동으로 만들고 안내 팝업을 표시합니다.

- 제목: `작업 폴더가 준비됐어요`
- 내용:
  - `SVG 파일을 svg 폴더에 저장하면`
  - `PNG가 자동으로 생성됩니다.`
  - `폴더 위치 : Desktop > figma_exports`

## 사용자 배포

기본 배포 파일:

- [응용이미지자동화 변환기.app](/Users/wy/Downloads/응용이미지자동변환기/dist/응용이미지자동화%20변환기.app)
- [응용이미지자동화 변환기.dmg](/Users/wy/Downloads/응용이미지자동변환기/release/응용이미지자동화%20변환기.dmg)

사용자 설치 흐름:

1. `.dmg` 열기
2. 앱을 `Applications`로 이동
3. 앱 실행
4. `Desktop/figma_exports/svg`에 SVG 저장

참고:

- 빌드 시 현재 Mac의 `Inkscape.app`을 번들에 포함합니다.
- 배포받는 사용자는 별도 Inkscape 설치가 필요 없습니다.

## 개발 환경 실행

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

## 앱 빌드

```bash
./build_app.sh
```

생성 결과:

- `dist/응용이미지자동화 변환기.app`

## DMG 빌드

```bash
./build_dmg.sh
```

생성 결과:

- `release/응용이미지자동화 변환기.dmg`

앱과 DMG를 한 번에 만들려면:

```bash
./build_release.sh
```

## 자동 업데이트

앱은 실행 시 `latest.json`을 확인해서 현재 버전보다 높은 버전이 있으면 다운로드 팝업을 표시합니다.

현재 업데이트 채널:

- `latest.json`:
  - [https://raw.githubusercontent.com/wefrqre/img-auto-converter/main/latest.json](https://raw.githubusercontent.com/wefrqre/img-auto-converter/main/latest.json)
- 다운로드 DMG:
  - [https://github.com/wefrqre/img-auto-converter/releases/download/v1.0.0/applied-image-auto-converter.dmg](https://github.com/wefrqre/img-auto-converter/releases/download/v1.0.0/applied-image-auto-converter.dmg)

앱에 포함되는 업데이트 URL 파일:

- [update_url.txt](/Users/wy/Downloads/응용이미지자동변환기/update_url.txt)

### GitHub 배포 방식

GitHub 저장소:

- [https://github.com/wefrqre/img-auto-converter](https://github.com/wefrqre/img-auto-converter)

자동 배포 스크립트:

- [publish_github_update.sh](/Users/wy/Downloads/응용이미지자동변환기/publish_github_update.sh)

실행 예시:

```bash
GH_TOKEN="ghp_xxx" RELEASE_NOTES="변경 내용" ./publish_github_update.sh
```

위 스크립트가 수행하는 작업:

1. `vAPP_VERSION` GitHub Release 생성 또는 갱신
2. DMG 업로드
3. 저장소 루트 `latest.json` 갱신

필수 권한:

- GitHub Personal Access Token
- 권한: `repo`

## 버전 올리는 방법

1. [app.py](/Users/wy/Downloads/응용이미지자동변환기/app.py) 의 `APP_VERSION` 수정
2. 앱 빌드
3. DMG 생성
4. `publish_github_update.sh` 실행

예:

```bash
GH_TOKEN="ghp_xxx" RELEASE_NOTES="UI 개선" ./publish_github_update.sh
```

## 로컬 업데이트 테스트

테스트 스크립트:

- [test_update_flow.sh](/Users/wy/Downloads/응용이미지자동변환기/test_update_flow.sh)

실행:

```bash
./test_update_flow.sh
```

이 스크립트는 테스트용 `latest.json`을 만들고 업데이트 팝업 동작을 확인합니다.

## PNG 결과 검증

```bash
magick identify -verbose ~/Desktop/figma_exports/png_96dpi/example.png | grep "Type"
magick identify -format "%k colors\n" ~/Desktop/figma_exports/png_96dpi/example.png
```

기대값:

- `Type: TrueColorAlpha`
- 색상 수가 256보다 큼

## 코드사인 / 노타리제이션

현재 프로젝트에는 서명/노타리제이션 준비 스크립트가 포함되어 있습니다.

- [release.env.example](/Users/wy/Downloads/응용이미지자동변환기/release.env.example)
- [setup_notary_profile.sh](/Users/wy/Downloads/응용이미지자동변환기/setup_notary_profile.sh)
- [sign_and_notarize.sh](/Users/wy/Downloads/응용이미지자동변환기/sign_and_notarize.sh)

외부 일반 사용자 배포 시에는 macOS 경고를 줄이기 위해 별도로 진행하는 것이 좋습니다.
