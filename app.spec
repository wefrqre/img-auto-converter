# -*- mode: python ; coding: utf-8 -*-


datas = [
    ("README.md", "."),
    ("bundle_bin", "bin"),
]


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="응용이미지자동화 변환기",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="응용이미지자동화 변환기",
)

app = BUNDLE(
    coll,
    name="응용이미지자동화 변환기.app",
    icon=None,
    bundle_identifier="com.local.applied-image-auto-converter",
)
