#!/bin/sh
# Build dist/SM809-x86_64.AppImage. The AppImage carries the sm809 code and uses
# the host's python3 (3.11+) and PyQt6 at runtime (python3-pyqt6 on Ubuntu/Debian).
set -e
cd "$(dirname "$0")"
RUNTIME=build/runtime-x86_64
APPDIR=build/AppDir
OUT=dist/SM809-x86_64.AppImage

if [ ! -f "$RUNTIME" ]; then
    mkdir -p build
    curl -fL -o "$RUNTIME" https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64
fi

rm -rf "$APPDIR" build/app.squashfs
mkdir -p "$APPDIR/usr/lib/sm809" dist
cp -r sm809 "$APPDIR/usr/lib/sm809/"
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +
cp sm809/icon.png "$APPDIR/sm809.png"
ln -s sm809.png "$APPDIR/.DirIcon"

cat > "$APPDIR/sm809.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=SM809 Pro
Comment=Configure the Solakaka SM809 Pro mouse
Exec=sm809 gui
Icon=sm809
Categories=Settings;HardwareSettings;
DESKTOP

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PYTHONPATH="$HERE/usr/lib/sm809${PYTHONPATH:+:$PYTHONPATH}"
PY="$(command -v python3)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "SM809 needs python3 3.11 or newer" >&2; exit 1
fi
[ $# -eq 0 ] && set -- gui
# -P: never let the current directory shadow the bundled code
exec "$PY" -P -m sm809 "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

mksquashfs "$APPDIR" build/app.squashfs -root-owned -noappend -comp zstd -quiet
# Write then rename, so rebuilding works while the old AppImage is running.
cat "$RUNTIME" build/app.squashfs > "$OUT.tmp"
chmod +x "$OUT.tmp"
mv -f "$OUT.tmp" "$OUT"
echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
