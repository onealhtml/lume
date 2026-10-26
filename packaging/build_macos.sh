#!/usr/bin/env bash
# Gera dist/Lume-<versao>-macOS-<arquitetura>.dmg (app sem assinatura da Apple).
#   LUME_VERSION=1.0.0 packaging/build_macos.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${LUME_VERSION:-0.0.0}"
PY="${PYTHON:-python3}"
ARCH="$(uname -m)"  # arm64 (Apple Silicon) ou x86_64 (Intel)

"$PY" -m PyInstaller packaging/lume.spec --noconfirm

# assinatura local (ad-hoc): sem ela o macOS em Apple Silicon recusa abrir o app
codesign --force --deep --sign - dist/Lume.app

# imagem de disco com o atalho para Aplicativos, o arrastar-e-soltar de sempre
STAGE=build/dmg
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R dist/Lume.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname Lume -srcfolder "$STAGE" -ov -format UDZO "dist/Lume-$VERSION-macOS-$ARCH.dmg"
ls -la dist/*.dmg
