#!/usr/bin/env bash
# Gera dist/Lume-<versao>-Linux-x86_64.AppImage e .tar.gz a partir do build do PyInstaller.
#   LUME_VERSION=1.0.0 packaging/build_linux.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${LUME_VERSION:-0.0.0}"
PY="${PYTHON:-python3}"

"$PY" -m PyInstaller packaging/lume.spec --noconfirm

# tar.gz: para quem nao quer AppImage
tar -C dist -czf "dist/Lume-$VERSION-Linux-x86_64.tar.gz" Lume

# AppImage: um arquivo so, executavel
APPDIR=build/Lume.AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -r dist/Lume/. "$APPDIR/usr/bin/"
cp assets/lume.png "$APPDIR/lume.png"
cat > "$APPDIR/lume.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Lume
Comment=Controle da lâmpada Tuya pela rede local
Exec=Lume
Icon=lume
Categories=Utility;
EOF
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/Lume" "$@"
EOF
chmod +x "$APPDIR/AppRun"

TOOL=build/appimagetool-x86_64.AppImage
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "dist/Lume-$VERSION-Linux-x86_64.AppImage"
ls -la dist/*.AppImage dist/*.tar.gz
