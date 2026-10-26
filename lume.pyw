"""Lume: controle de lampada Tuya (Positivo, Smart Life, etc.) pela rede local.
Vive na bandeja (Windows/Linux) ou na barra de menus (macOS): clique no icone abre o painel rapido."""
import io
import os
import sys
import threading
import time

import tinytuya
from PySide6.QtCore import QObject, QPoint, QRect, Property, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QFont, QFontDatabase, QGuiApplication, QIcon, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import core
import platform_tweaks
import smartlife_qr
from icons import tray_icon

APP_DIR = os.path.dirname(os.path.abspath(__file__))
QML_PATH = os.path.join(APP_DIR, "ui", "Main.qml")
ICON_PATH = os.path.join(APP_DIR, "assets", "lume.png")
EDGE = 12  # distancia do painel ate a borda da area de trabalho

# primeira fonte instalada vence; no macOS a do sistema ja e a SF Pro
FONT_CHOICES = ["SF Pro Text", "SF Pro Display", "Inter", "Segoe UI Variable Text", "Cantarell", "Ubuntu"]
DISPLAY_CHOICES = ["SF Pro Display", "Inter Display", "Inter", "Segoe UI Variable Display", "Cantarell", "Ubuntu"]
ROUNDED_CHOICES = ["SF Pro Rounded", "SF Pro Display", "Inter", "Segoe UI Variable Display"]


def pick_font(choices):
    installed = set(QFontDatabase.families())
    return next((f for f in choices if f in installed), QGuiApplication.font().family())


def pil_to_icon(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    pix = QPixmap()
    pix.loadFromData(buf.getvalue(), "PNG")
    return QIcon(pix)


class Backend(QObject):
    """Ponte com o QML (objeto `backend`). Rede e lampada rodam em threads; o resultado volta por sinal."""

    stateChanged = Signal()
    showRequested = Signal(str)      # view: "main" ou "settings"
    scanFinished = Signal("QVariantList")
    smartlifeQr = Signal("QVariantMap")
    smartlifeResult = Signal("QVariantMap")
    _incoming = Signal("QVariantMap")  # estado vindo de outra thread

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._state = {}
        self._incoming.connect(self._set_state)

    @Property("QVariantMap", notify=stateChanged)
    def state(self):
        return self._state

    @Slot("QVariantMap")
    def _set_state(self, st):
        self._state = st
        self.stateChanged.emit()
        self._app.update_tray(st)

    @Property(str, constant=True)
    def textFont(self):
        return pick_font(FONT_CHOICES)

    @Property(str, constant=True)
    def displayFont(self):
        return pick_font(DISPLAY_CHOICES)

    @Property(str, constant=True)
    def roundedFont(self):
        return pick_font(ROUNDED_CHOICES)

    @Property(bool, notify=stateChanged)
    def systemGlass(self):
        return self._app.system_glass

    @Property(bool, constant=True)
    def isWindows(self):
        return platform_tweaks.IS_WIN

    @Slot(str)
    def setGlass(self, mode):
        """Troca o vidro na hora (configuracoes > Vidro) e guarda a escolha."""
        self._app.cfg["glass"] = mode
        core.save_config(self._app.cfg)
        self._app.apply_glass()

    @Property(bool, notify=stateChanged)
    def windowed(self):
        """Sem bandeja o painel vira janela comum, com moldura do sistema: fundo solido e reto."""
        return not self._app.has_tray

    @Property(str, constant=True)
    def wheelUrl(self):
        return QUrl.fromLocalFile(os.path.join(APP_DIR, "assets", "wheel.png")).toString()

    # ----- comandos: False = lampada desconectada -----
    @Slot(bool, result=bool)
    def power(self, on):
        return self._app.lamp.power(on)

    @Slot(int, result=bool)
    def brightness(self, pct):
        return self._app.lamp.brightness(pct)

    @Slot(int, result=bool)
    def temperature(self, pct):
        return self._app.lamp.temperature(pct)

    @Slot(int, int, result=bool)
    def colour(self, h, s):
        return self._app.lamp.colour(h, s)

    @Slot()
    def hide(self):
        self._app.hide_panel()

    @Slot()
    def windowClosed(self):
        # com bandeja fechar so esconde; sem bandeja nao ha como reabrir, entao sai
        self._app.hide_panel() if self._app.tray else self._app.quit()

    @Slot()
    def quit(self):
        self._app.quit()

    # ----- configuracoes -----
    @Slot(result="QVariantMap")
    def getConfig(self):
        return {**self._app.cfg, "autostart": core.autostart_enabled()}

    @Slot("QVariantMap")
    def saveConfig(self, cfg):
        old = self._app.cfg
        new = {k: str(cfg.get(k, core.DEFAULT_CONFIG[k])).strip() for k in core.DEFAULT_CONFIG}
        new["last_ip"] = old.get("last_ip", "") if new["id"] == old["id"] else ""
        new["glass"] = old.get("glass", core.DEFAULT_CONFIG["glass"])  # escolhido a parte, ja salvo
        self._app.cfg = new
        core.save_config(new)
        self._app.lamp.connect()

    @Slot(bool, result=bool)
    def setAutostart(self, enable):
        try:
            core.set_autostart(enable)
        except OSError:
            pass
        return core.autostart_enabled()

    @Slot()
    def scan(self):
        def job():
            try:
                found = tinytuya.deviceScan(verbose=False, maxretry=15)
            except Exception:  # noqa: BLE001 - porta UDP ocupada etc.
                found = {}
            self.scanFinished.emit([{"ip": ip, "id": d.get("gwId", ""), "version": str(d.get("version", ""))}
                                    for ip, d in found.items()])
        threading.Thread(target=job, daemon=True).start()

    @Slot(str)
    def smartlifeStart(self, user_code):
        def job():
            try:
                self.smartlifeQr.emit(smartlife_qr.start(user_code.strip()))
            except Exception as e:  # noqa: BLE001
                self.smartlifeQr.emit({"error": str(e)})
        threading.Thread(target=job, daemon=True).start()

    @Slot(str, str)
    def smartlifePoll(self, token, user_code):
        def job():
            try:
                devices = smartlife_qr.poll(token, user_code.strip())
                self.smartlifeResult.emit({"pending": True} if devices is None else {"devices": devices})
            except Exception as e:  # noqa: BLE001
                self.smartlifeResult.emit({"error": f"Falha no login: {e}"})
        threading.Thread(target=job, daemon=True).start()


class App:
    def __init__(self):
        platform_tweaks.before_app()
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        self.qapp = QApplication(sys.argv)
        self.qapp.setApplicationName("Lume")
        self.qapp.setQuitOnLastWindowClosed(False)
        self.qapp.setWindowIcon(QIcon(ICON_PATH))
        platform_tweaks.after_app()

        self.cfg = core.load_config()
        self.visible = False
        self.hidden_at = 0.0
        self.was_active = False
        self.system_glass = False
        self.tray_rgb = "unset"

        # sem area de bandeja (GNOME sem a extensao AppIndicator, WSL...): vira janela comum
        self.has_tray = QSystemTrayIcon.isSystemTrayAvailable()
        self.backend = Backend(self)
        self.qapp.setFont(QFont(self.backend.textFont))
        self.engine = QQmlApplicationEngine()
        self.engine.rootContext().setContextProperty("backend", self.backend)
        self.engine.load(QUrl.fromLocalFile(QML_PATH))
        if not self.engine.rootObjects():
            raise SystemExit("Falha ao carregar ui/Main.qml")
        self.window = self.engine.rootObjects()[0]
        self.window.activeChanged.connect(self._on_active_changed)
        if not self.has_tray:
            self.window.setFlags(Qt.WindowType.Window)

        self.tray = None
        if self.has_tray:
            self._make_tray()

        self.lamp = core.Lamp(lambda: self.cfg, self.on_state)
        self.lamp.connect()
        if not self.cfg["key"] or not self.has_tray:
            self.show_panel("settings" if not self.cfg["key"] else "main")

    def _make_tray(self):
        self.tray = QSystemTrayIcon(pil_to_icon(tray_icon()))
        self.tray.setToolTip("Lume")
        self.tray.activated.connect(self._on_tray)
        if not platform_tweaks.IS_MAC:
            # no macOS um menu no icone substitui o clique; la o "Fechar" fica nas configuracoes
            menu = QMenu()
            menu.addAction("Abrir", self.toggle_panel)
            self.power_action = menu.addAction("Ligar", lambda: self.lamp.power(not self.lamp.state["on"]))
            menu.addSeparator()
            menu.addAction("Fechar o Lume", self.quit)
            self.menu = menu
            self.tray.setContextMenu(menu)
        self.tray.show()

    def run(self):
        rc = self.qapp.exec()
        # destroi o QML antes do backend, senao as ligacoes leem um `backend` ja apagado ao sair
        import shiboken6
        shiboken6.delete(self.engine)
        return rc

    # ----- estado -----
    def on_state(self, state):
        # pode vir de qualquer thread: o sinal leva para a thread da interface (Backend._set_state)
        self.backend._incoming.emit(state)

    def update_tray(self, state):
        if not self.tray:
            return
        rgb = self.lamp.tray_rgb() if hasattr(self, "lamp") else None
        if rgb != self.tray_rgb:  # recriar o icone a cada arrasto do slider e caro
            self.tray_rgb = rgb
            self.tray.setIcon(pil_to_icon(tray_icon(rgb)))
        status = state["status"] or ("acesa" if state["on"] else "apagada")
        self.tray.setToolTip(f"Lume – {self.cfg['name']} {status}"[:127])
        if hasattr(self, "power_action"):
            self.power_action.setText("Desligar" if state["on"] else "Ligar")

    # ----- painel -----
    def _on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_panel()

    def toggle_panel(self):
        if self.visible:
            self.hide_panel()
        elif time.time() - self.hidden_at > 0.4:  # o clique no icone acabou de fechar o painel
            self.show_panel()

    def _anchor(self):
        """Ponto de onde o painel nasce: o icone da bandeja, ou o cursor se o sistema nao informa."""
        geo = self.tray.geometry()
        if geo.isValid() and not geo.isEmpty():
            return geo
        p = QCursor.pos()
        return QRect(p, p)

    def _place(self):
        if not self.tray:  # janela comum: centro da tela
            area = QGuiApplication.primaryScreen().availableGeometry()
            self.window.setPosition(area.center() - QPoint(self.window.width() // 2, self.window.height() // 2))
            return
        icon = self._anchor()
        c = icon.center()
        screen = QGuiApplication.screenAt(c) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        w, h = self.window.width(), self.window.height()
        if area.contains(c):
            # icone dentro da area util (bandeja estendida, Wayland sem posicao...): abre ao lado
            x = icon.right() + 8 if c.x() < area.center().x() else icon.left() - w - 8
            y = icon.bottom() - h if c.y() > area.center().y() else icon.top()
        elif c.y() < area.top():      # barra no topo (macOS, alguns Linux): abre embaixo do icone
            x, y = c.x() - w // 2, area.top() + EDGE
        else:                          # barra embaixo/lado: o limite da area deixa colado nela
            x, y = c.x() - w // 2, c.y() - h // 2
        x = min(max(x, area.left() + EDGE), area.right() - w - EDGE)
        y = min(max(y, area.top() + EDGE), area.bottom() - h - EDGE)
        self.window.setPosition(QPoint(x, y))

    def show_panel(self, view="main"):
        self._place()
        self.visible = True
        self.was_active = False
        self.window.show()
        win_id = int(self.window.winId())
        if not self.system_glass:
            self.apply_glass()
        self.window.raise_()
        self.window.requestActivate()
        platform_tweaks.focus_window(win_id)
        self.backend.showRequested.emit(view)
        threading.Thread(target=self.lamp.refresh, daemon=True).start()

    def apply_glass(self):
        mode = os.environ.get("LUME_GLASS") or self.cfg.get("glass", "acrylic")
        ok = platform_tweaks.style_window(int(self.window.winId()), mode)
        if ok != self.system_glass:
            self.system_glass = ok
            self.backend.stateChanged.emit()

    def hide_panel(self):
        if not self.tray:  # sem bandeja nao ha como reabrir: so minimiza
            self.window.showMinimized()
            return
        if self.visible:
            self.visible = False
            self.hidden_at = time.time()
            self.window.hide()

    def _on_active_changed(self):
        # fecha ao clicar fora (o painel perdeu o foco depois de ter tido)
        if self.window.isActive():
            self.was_active = True
        elif self.visible and self.was_active and self.tray:
            self.hide_panel()

    def quit(self):
        if self.tray:
            self.tray.hide()
        self.qapp.quit()


if __name__ == "__main__":
    sys.exit(App().run())
