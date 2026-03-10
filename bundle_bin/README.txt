Optional bundled executables for distribution.

If you want the app to prefer bundled tools before checking PATH, place
executable files here with these names:

- inkscape
- magick

At runtime, the app checks:
1. Contents/Resources/bin/
2. Contents/Resources/tools/bin/
3. Standard system locations and PATH

This directory is included in the PyInstaller build via app.spec.

build_app.sh behavior:
- By default (`BUNDLE_INKSCAPE=1`), the script copies local Inkscape.app
  into `bundle_bin/vendor/Inkscape.app` before building.
- Set `BUNDLE_INKSCAPE=0` to skip auto-bundling.
