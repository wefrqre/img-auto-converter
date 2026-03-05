# -*- mode: python ; coding: utf-8 -*-


datas = [
    ("README.md", "."),
    ("bundle_bin", "bin"),
    ("app_icon.png", "."),
    ("info.svg", "."),
    ("arrow_down.svg", "."),
    ("Vector.svg", "."),
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
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="응용이미지자동화 변환기",
)
