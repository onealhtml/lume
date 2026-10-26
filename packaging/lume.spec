# PyInstaller: gera dist/Lume (Windows/Linux) ou dist/Lume.app (macOS).
#   pyinstaller packaging/lume.spec --noconfirm
# Versao: variavel de ambiente LUME_VERSION (o workflow de release usa a tag).
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = os.environ.get("LUME_VERSION", "0.0.0")
IS_MAC = sys.platform == "darwin"

a = Analysis(
    [os.path.join(ROOT, "lume.pyw")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "ui"), "ui"), (os.path.join(ROOT, "assets"), "assets")],
    hiddenimports=["PySide6.QtQuick", "PySide6.QtSvg", "tuya_sharing"],
    # o app nao usa nada disso; deixa o pacote bem menor
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
              "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtMultimedia",
              "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lume",
    console=False,
    icon=os.path.join(ROOT, "assets", "lume.png" if IS_MAC else "lume.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Lume")

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Lume.app",
        icon=os.path.join(ROOT, "assets", "lume.png"),
        bundle_identifier="ai.lume.tray",
        info_plist={
            "CFBundleName": "Lume",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSUIElement": True,  # so na barra de menus, sem icone no Dock
            "NSHighResolutionCapable": True,
            # macOS 15+ pede permissao para falar com aparelhos da rede local
            "NSLocalNetworkUsageDescription": "Lume talks to your lamp over the local network.",
        },
    )
