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
    hiddenimports=["PySide6.QtQuick", "PySide6.QtSvg", "tuya_sharing"]
    # cofre do sistema (login do Smart Life): o keyring acha os backends por entry points
    + ([] if sys.platform == "win32" else ["keyring.backends.macOS", "keyring.backends.SecretService"]),
    # o app nao usa nada disso; deixa o pacote bem menor
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
              "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtMultimedia",
              "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner"],
    noarchive=False,
)

if sys.platform.startswith("linux"):
    # Libs que tem que vir da distro: as do build (Ubuntu 22.04) sao velhas e quebram as do sistema
    # carregadas no mesmo processo. Ex.: o Mesa do Fedora 44 pede GLIBCXX_3.4.32, nosso libstdc++ nao tem,
    # o EGL nao carrega e o Qt Quick aborta ("Failed to initialize graphics backend for OpenGL").
    HOST_LIBS = {
        "libstdc++", "libgcc_s", "libexpat", "libz", "libzstd", "liblzma",
        "libX11", "libX11-xcb", "libXau", "libXdmcp", "libXext", "libXrender", "libXfixes", "libXi",
        "libXrandr", "libXcursor", "libXinerama", "libXcomposite", "libXdamage",
        "libxcb-glx", "libxcb-randr", "libxcb-render", "libxcb-shape", "libxcb-shm", "libxcb-sync",
        "libxcb-xfixes", "libxcb-xkb",
        "libglib-2.0", "libgio-2.0", "libgobject-2.0", "libgmodule-2.0", "libgthread-2.0",
        "libpcre", "libpcre2-8", "libmount", "libblkid", "libselinux",
        # GTK do tema do Qt (libqgtk3): usa o GTK do sistema, ou o plugin simplesmente nao carrega
        "libgtk-3", "libgdk-3", "libgdk_pixbuf-2.0", "libatk-1.0", "libatk-bridge-2.0", "libatspi",
        "libcairo", "libcairo-gobject", "libpango-1.0", "libpangocairo-1.0", "libpangoft2-1.0",
        "libepoxy", "libharfbuzz", "libfribidi", "libthai", "libdatrie", "libgraphite2", "libpixman-1",
        "libfontconfig", "libfreetype", "libpng16", "libjpeg",
    }
    # so o nome exato: as libs do Pillow tem hash no nome (libxcb-9065bb12.so...) e ficam
    a.binaries = [b for b in a.binaries if os.path.basename(b[0]).split(".so")[0] not in HOST_LIBS]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lume",
    console=False,
    icon=os.path.join(ROOT, "assets", "lume.icns" if IS_MAC else "lume.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Lume")

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Lume.app",
        icon=os.path.join(ROOT, "assets", "lume.icns"),
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
