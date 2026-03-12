# -*- mode: python ; coding: utf-8 -*-


datas = [
    ("README.md", "."),
    ("bundle_bin", "bin"),
    ("info.svg", "."),
    ("arrow-down.svg", "."),
    ("arrow-up.svg", "."),
    ("Figma.svg", "."),
    ("Local.svg", "."),
    ("loading.svg", "."),
    ("update_url.txt", "."),
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
