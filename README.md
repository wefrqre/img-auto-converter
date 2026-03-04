# 응용이미지자동화 변환기

macOS용 로컬 GUI 앱입니다. 입력 폴더에 들어온 Figma Export SVG를 감지해서, 응용 환경 요구사항에 맞는 PNG로 자동 변환합니다.

## 지원 기능

- 첫 실행 시 `~/figma_exports/svg` 와 `~/figma_exports/png_96dpi` 자동 생성
- 입력/출력 폴더 선택 UI
- 마지막 선택 경로를 `~/.applied_image_auto_converter.json` 에 저장 후 재실행 시 복원
- `watchdog` 기반 폴더 감시
- `watchdog` 미설치 시 내장 폴링 감시로 자동 대체
- `Inkscape` + `ImageMagick` 파이프라인 실행
- Finder에서 더블클릭 실행되는 `.app` 환경을 고려해 PATH 자동 보정
  - `/opt/homebrew/bin`
  - `/usr/local/bin`
  - `/usr/bin`
  - `/bin`
- PyInstaller 번들 내부 `Contents/Resources/bin` 도 우선 탐지

## 변환 스펙

1. `inkscape` 로 SVG를 임시 PNG로 렌더링
   - `--export-background-opacity=0`
2. `magick` 으로 최종 PNG 저장
   - `-units PixelsPerInch`
   - `-density 96`
   - `-alpha on`
   - `-background none`
   - `-type TrueColorAlpha`
   - `-define png:color-type=6`

목표 결과:

- PNG
- 96dpi(PPI 메타데이터)
- TrueColorAlpha(RGBA)
- Indexed(팔레트) PNG 방지
- 투명 배경 유지

## 다른 사람 컴퓨터에 배포할 때

이 앱은 **기본 폴더를 자동 생성할 수 있습니다.** 앱을 처음 실행하면 아래 폴더가 없을 경우 자동으로 만듭니다.

- `~/figma_exports/svg`
- `~/figma_exports/png_96dpi`

즉, 사용자에게 미리 폴더를 수동으로 만들라고 할 필요는 없습니다. 앱을 켠 뒤 바로 SVG를 입력 폴더에 넣으면 됩니다.

## 필수 설치 항목

현재 버전은 기본적으로 `Inkscape` 와 `ImageMagick` 을 시스템에 설치해두는 방식입니다. 앱은 설치 여부를 확인하고, 없으면 안내 팝업을 띄웁니다.

`watchdog` 는 선택 사항입니다. 설치되어 있으면 파일 변경 감지가 더 즉시 반응하고, 없어도 앱은 내장 폴링 모드로 계속 동작합니다.

권장 설치:

```bash
brew install --cask inkscape
brew install imagemagick
```

장기적으로 더 완전한 배포를 원하면, 다음 단계로 `Inkscape`/`ImageMagick` 을 별도 설치 없이 동작하도록 앱 번들링 또는 설치 프로그램화할 수 있습니다. 다만 그 작업은 앱 용량, 라이선스, 코드사인/노타리제이션 검토가 추가로 필요합니다.

## 번들 내부 실행 파일 포함 옵션

별도 설치 없이 배포하고 싶다면, 빌드 전에 [bundle_bin/README.txt](/Users/wy/Downloads/응용이미자동변환기/bundle_bin/README.txt)에 맞춰 아래 파일을 `bundle_bin/` 폴더에 넣을 수 있습니다.

- `bundle_bin/inkscape`
- `bundle_bin/magick`

그러면 앱이 실행될 때 시스템 PATH보다 먼저 번들 내부 실행 파일을 찾습니다.

## 개발 환경 실행

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

`watchdog` 설치 없이 실행만 해보려면:

```bash
python3 app.py
```

## .app 빌드

```bash
chmod +x build_app.sh
./build_app.sh
```

생성 결과:

- `dist/응용이미지자동화 변환기.app`

## 릴리스 패키지(.dmg) 빌드

```bash
chmod +x build_dmg.sh build_release.sh
./build_release.sh
```

생성 결과:

- `dist/응용이미지자동화 변환기.app`
- `release/응용이미지자동화 변환기.dmg`

DMG는 사용자가 앱을 드래그해서 `/Applications` 로 옮기는 일반적인 macOS 배포 방식입니다.

## 코드사인 / 노타리제이션

외부 배포용이면 macOS Gatekeeper 경고를 줄이기 위해 코드사인과 노타리제이션을 권장합니다.

이 프로젝트에는 준비용 스크립트가 포함되어 있습니다.

- [release.env.example](/Users/wy/Downloads/응용이미자동변환기/release.env.example)
- [setup_notary_profile.sh](/Users/wy/Downloads/응용이미자동변환기/setup_notary_profile.sh)
- [sign_and_notarize.sh](/Users/wy/Downloads/응용이미자동변환기/sign_and_notarize.sh)

진행 순서:

1. `release.env.example` 를 `release.env` 로 복사 후 값 입력
2. Apple 계정용 app-specific password 생성
3. notarytool keychain profile 저장
4. 앱 빌드
5. 코드사인 + 노타리제이션 실행

예시:

```bash
cp release.env.example release.env
chmod +x setup_notary_profile.sh sign_and_notarize.sh
./setup_notary_profile.sh
./build_release.sh
./sign_and_notarize.sh
```

`release.env` 에 들어가는 핵심 값:

- `DEVELOPER_ID_APPLICATION`
- `APPLE_ID`
- `TEAM_ID`
- `APP_SPECIFIC_PASSWORD`
- `KEYCHAIN_PROFILE`

주의:

- 이 Mac에는 현재 `Developer ID Application` 인증서가 없으면 실제 서명은 실패합니다.
- `xcrun notarytool` 은 Xcode Command Line Tools 또는 Xcode 설치가 필요합니다.
- `release.env` 에는 민감한 정보가 들어가므로 git에 올리면 안 됩니다.

## 검증 예시

```bash
magick identify -verbose ~/figma_exports/png_96dpi/example.png | grep "Type"
magick identify -format "%k colors\n" ~/figma_exports/png_96dpi/example.png
```

기대값:

- `Type: TrueColorAlpha`
- 색상 수가 256보다 충분히 큼

## 배포 안정화 다음 단계

다른 사람 컴퓨터에서 더 매끄럽게 배포하려면 아래를 추가로 고려하면 됩니다.

1. 앱 서명(Code Signing)
2. Apple notarization
3. `bundle_bin` 에 실제 실행 파일을 넣는 방식 또는 별도 설치 방식 중 하나로 배포 정책 확정
4. 배포용 안내 문구(최초 실행, 보안 경고 우회법) 추가
