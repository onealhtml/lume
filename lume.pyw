"""Lume: controle de lampada Tuya (Positivo, Smart Life, etc.) pela rede local.
Vive na bandeja (Windows/Linux) ou na barra de menus (macOS): clique no icone abre o painel rapido."""
import io
import os
import sys
import threading
import time

import tinytuya
from PySide6.QtCore import QLocale, QObject, QPoint, QRect, Property, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCursor, QFont, QFontDatabase, QGuiApplication, QIcon, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import core
import i18n
import platform_tweaks
import smartlife_qr
from i18n import _
from icons import tray_icon

APP_DIR = core.APP_DIR
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

    stateChanged = Signal()   # estado do que o painel controla (uma lampada ou todas)
    lampsChanged = Signal()   # lista de lampadas (nomes, ligadas, conectadas) ou selecao
    showRequested = Signal(str)      # view: "main" ou "settings"
    scanFinished = Signal("QVariantList")
    smartlifeQr = Signal("QVariantMap")
    smartlifeResult = Signal("QVariantMap")  # {pending} | {devices, remembered} | {expired} | {error}
    importFinished = Signal("QVariantMap")
    smartlifeChanged = Signal()
    _incoming = Signal("QVariantMap")  # estado de uma lampada vindo de outra thread
    _scanned = Signal("QVariantList", "QVariantMap")  # busca da importacao pronta (volta para a interface)

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._incoming.connect(self._lamp_state)
        self._scanned.connect(self._finish_import)
        # ler o login guardado decifra um arquivo (ou fala com o chaveiro no Linux): so uma vez,
        # e depois a cada entrar/sair/expirar; nunca a cada atualizacao das lampadas
        self._signed_in = smartlife_qr.signed_in()

    @Slot("QVariantMap")
    def _lamp_state(self, st):
        if st["id"] not in self._app.lamps:  # aviso atrasado de uma lampada ja removida
            return
        self._app.lamp_states[st["id"]] = st
        self.stateChanged.emit()
        self.lampsChanged.emit()
        self._app.update_tray()

    @Property("QVariantMap", notify=stateChanged)
    def state(self):
        return self._app.view_state()

    @Property("QVariantList", notify=lampsChanged)
    def lamps(self):
        return [{"id": c["id"], "name": self._app.lamp_name(c), "connected": bool(st.get("connected")),
                 "on": bool(st.get("connected") and st.get("on"))}
                for c in self._app.cfg["lamps"] for st in [self._app.lamp_states.get(c["id"], {})]]

    @Property(str, notify=lampsChanged)
    def selected(self):
        return self._app.selected()

    @Slot(str)
    def select(self, lamp_id):
        self._app.select(lamp_id)

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

    @Property("QVariantMap", constant=True)
    def strings(self):
        """Traducoes do idioma do sistema (texto em ingles -> traduzido); vazio em ingles."""
        return i18n.table()

    @Property(bool, constant=True)
    def isWindows(self):
        return platform_tweaks.IS_WIN

    @Slot(str)
    def setGlass(self, mode):
        """Troca o vidro na hora (configuracoes > Vidro) e guarda a escolha."""
        self._app.cfg["glass"] = mode
        self._app.save()
        self._app.apply_glass()

    @Property(bool, notify=stateChanged)
    def windowed(self):
        """Sem bandeja o painel vira janela comum, com moldura do sistema: fundo solido e reto."""
        return not self._app.has_tray

    @Property(str, constant=True)
    def wheelUrl(self):
        return QUrl.fromLocalFile(os.path.join(APP_DIR, "assets", "wheel.png")).toString()

    # ----- comandos (na lampada escolhida ou em todas): False = nenhuma conectada -----
    @Slot(bool, result=bool)
    def power(self, on):
        return self._app.command("power", on)

    @Slot(int, result=bool)
    def brightness(self, pct):
        return self._app.command("brightness", pct)

    @Slot(int, result=bool)
    def temperature(self, pct):
        return self._app.command("temperature", pct)

    @Slot(int, int, result=bool)
    def colour(self, h, s):
        return self._app.command("colour", h, s)

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
        return {"glass": self._app.cfg["glass"], "lamps": [dict(c) for c in self._app.cfg["lamps"]],
                "autostart": core.autostart_enabled()}

    @Slot("QVariantList")
    def saveLamps(self, lamps):
        self._app.set_lamps(lamps)

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
            self.scanFinished.emit([{"ip": ip, "id": d.get("gwId", ""), "version": str(d.get("version", ""))}
                                    for ip, d in scan_network().items()])
        threading.Thread(target=job, daemon=True).start()

    # ----- Smart Life -----
    @Property(bool, notify=smartlifeChanged)
    def smartlifeSignedIn(self):
        return self._signed_in

    def _recheck_signed_in(self):
        self._signed_in = smartlife_qr.signed_in()
        self.smartlifeChanged.emit()

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
                r = smartlife_qr.poll(token, user_code.strip())
                self.smartlifeResult.emit({"pending": True} if r is None else r)
            except Exception as e:  # noqa: BLE001
                self.smartlifeResult.emit({"error": _("Login failed: %s") % e})
            self._recheck_signed_in()
        threading.Thread(target=job, daemon=True).start()

    @Slot()
    def smartlifeDevices(self):
        """Lista as lampadas da conta com o login guardado (sem QR)."""
        def job():
            try:
                self.smartlifeResult.emit({"devices": smartlife_qr.devices(), "remembered": True})
            except smartlife_qr.SessionExpired:
                self.smartlifeResult.emit({"expired": True})
            except Exception as e:  # noqa: BLE001
                self.smartlifeResult.emit({"error": _("Login failed: %s") % e})
            self._recheck_signed_in()
        threading.Thread(target=job, daemon=True).start()

    @Slot()
    def smartlifeSignOut(self):
        self._signed_in = False  # a tela muda na hora; o servidor e avisado em segundo plano
        self.smartlifeChanged.emit()

        def job():
            smartlife_qr.sign_out()
        threading.Thread(target=job, daemon=True).start()

    @Slot("QVariantList")
    def importLamps(self, devices):
        """Adiciona as lampadas escolhidas; procura cada uma na rede para ja saber IP e versao."""
        devices = [{"id": d["id"], "key": d["key"], "name": d["name"]} for d in devices]

        def job():
            found = {d.get("gwId"): [ip, str(d.get("version", ""))] for ip, d in scan_network().items()}
            self._scanned.emit(devices, found)
        threading.Thread(target=job, daemon=True).start()

    @Slot("QVariantList", "QVariantMap")
    def _finish_import(self, devices, found):
        self.importFinished.emit(self._app.import_lamps(devices, found))


def scan_network():
    try:
        return tinytuya.deviceScan(verbose=False, maxretry=15)
    except Exception:  # noqa: BLE001 - porta UDP ocupada etc.
        return {}


class App:
    def __init__(self):
        platform_tweaks.before_app()
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        self.qapp = QApplication(sys.argv)
        self.qapp.setApplicationName("Lume")
        self.qapp.setDesktopFileName(core.DESKTOP_ID)  # Linux: a janela e do app Lume, nao de quem o abriu
        core.install_desktop_entry()
        self.qapp.setQuitOnLastWindowClosed(False)
        self.qapp.setWindowIcon(QIcon(ICON_PATH))
        platform_tweaks.after_app()
        i18n.set_lang(i18n.detect(QLocale.system().uiLanguages()))

        self.cfg = core.load_config()
        self.lamps = {}         # id -> core.Lamp
        self.lamp_states = {}   # id -> ultimo estado recebido
        self.save_lock = threading.Lock()
        self.visible = False
        self.hidden_at = 0.0
        self.was_active = False
        self.system_glass = False
        self.tray_rgb = "unset"
        self.menu_lamps = None

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

        self.sync_lamps()
        configured = any(c["key"] for c in self.cfg["lamps"])
        if not configured or not self.has_tray:
            self.show_panel("main" if configured else "settings")

    def _make_tray(self):
        self.tray = QSystemTrayIcon(pil_to_icon(tray_icon()))
        self.tray.setToolTip("Lume")
        self.tray.activated.connect(self._on_tray)
        if not platform_tweaks.IS_MAC:
            # no macOS um menu no icone substitui o clique; la o "Fechar" fica nas configuracoes
            self.menu = QMenu()
            self.menu.aboutToShow.connect(self._fill_menu)
            self._fill_menu()
            self.tray.setContextMenu(self.menu)
        self.tray.show()

    def _fill_menu(self):
        """Abrir; uma linha por lampada (marcada = acesa, clique liga/desliga); Fechar."""
        self.menu.clear()
        self.menu.addAction(_("Open"), self.toggle_panel)
        self.menu.addSeparator()
        for c in self.cfg["lamps"]:
            st = self.lamp_states.get(c["id"], {})
            act = self.menu.addAction(self.lamp_name(c))
            act.setCheckable(True)
            act.setChecked(bool(st.get("connected") and st.get("on")))
            act.setEnabled(bool(st.get("connected")))
            act.triggered.connect(lambda on, lid=c["id"]: self.lamps[lid].power(on))
        self.menu.addSeparator()
        self.menu.addAction(_("Quit Lume"), self.quit)

    def run(self):
        rc = self.qapp.exec()
        # destroi o QML antes do backend, senao as ligacoes leem um `backend` ja apagado ao sair
        import shiboken6
        shiboken6.delete(self.engine)
        return rc

    def save(self):
        with self.save_lock:
            core.save_config(self.cfg)

    # ----- lampadas -----
    def lamp_cfg(self, lamp_id):
        return next((c for c in self.cfg["lamps"] if c["id"] == lamp_id), dict(core.LAMP_DEFAULTS, id=lamp_id))

    def lamp_name(self, c):
        if c["name"]:
            return c["name"]
        n = len(self.cfg["lamps"])
        return _("Lamp") if n == 1 else f"{_('Lamp')} {self.cfg['lamps'].index(c) + 1}"

    def sync_lamps(self, reconnect=()):
        """Cria/fecha conexoes para bater com a lista da configuracao; reconecta as ids em `reconnect`."""
        ids = [c["id"] for c in self.cfg["lamps"]]
        for lid in list(self.lamps):
            if lid not in ids:
                self.lamps.pop(lid).close()
                self.lamp_states.pop(lid, None)
        for lid in ids:
            if lid not in self.lamps:
                self.lamps[lid] = core.Lamp(lambda lid=lid: self.lamp_cfg(lid), self.on_state, self.save)
                self.lamps[lid].connect()
            elif lid in reconnect:
                self.lamps[lid].connect()
        self.backend.lampsChanged.emit()
        self.backend.stateChanged.emit()
        self.update_tray()

    def set_lamps(self, lamps):
        """Lista editada nas configuracoes."""
        old = {c["id"]: c for c in self.cfg["lamps"]}
        new, seen = [], set()
        for lamp in lamps:
            c = {k: str(lamp.get(k, v)).strip() for k, v in core.LAMP_DEFAULTS.items()}
            if not c["id"] or c["id"] in seen:
                continue
            seen.add(c["id"])
            prev = old.get(c["id"])
            if prev and c["ip"] == prev["ip"]:
                c["last_ip"] = prev["last_ip"]
            new.append(c)
        changed = [c["id"] for c in new if c["id"] in old
                   and any(c[k] != old[c["id"]][k] for k in ("key", "ip", "version"))]
        sel = self.cfg.get("selected", "")
        if sel and sel != core.ALL and sel not in seen:  # ID trocado na mesma linha: a selecao vai junto
            pos = [c["id"] for c in self.cfg["lamps"]].index(sel) if sel in old else -1
            self.cfg["selected"] = new[pos]["id"] if 0 <= pos < len(new) else ""
        self.cfg["lamps"] = new
        self.save()
        self.sync_lamps(reconnect=changed)

    def import_lamps(self, devices, found):
        """Lampadas vindas do Smart Life; `found` = id -> (ip, versao) achados na rede agora."""
        added = 0
        for d in devices:
            c = self.lamp_cfg(d["id"])
            if c not in self.cfg["lamps"]:
                self.cfg["lamps"].append(c)
                added += 1
            c.update(key=d["key"], name=c["name"] or d["name"])
            if d["id"] in found:
                ip, version = found[d["id"]]
                c["last_ip"] = ip
                if version:
                    c["version"] = version
        self.save()
        self.sync_lamps(reconnect=[d["id"] for d in devices])
        return {"added": added, "updated": len(devices) - added, "ids": [d["id"] for d in devices],
                "missing": [d["name"] for d in devices if d["id"] not in found]}

    def selected(self):
        ids = [c["id"] for c in self.cfg["lamps"]]
        sel = self.cfg.get("selected", "")
        if sel == core.ALL and len(ids) > 1 or sel in ids:
            return sel
        return ids[0] if ids else ""

    def select(self, lamp_id):
        self.cfg["selected"] = lamp_id
        self.save()
        self.backend.lampsChanged.emit()
        self.backend.stateChanged.emit()
        self.update_tray()

    def targets(self):
        sel = self.selected()
        if sel == core.ALL:
            return list(self.lamps.values())
        return [self.lamps[sel]] if sel in self.lamps else []

    def command(self, name, *args):
        results = [getattr(lamp, name)(*args) for lamp in self.targets()]
        return any(results)

    def view_state(self):
        """O que o painel mostra: a lampada escolhida, ou em "Todas" uma de referencia (cor, brilho)
        com "acesa" se qualquer uma estiver acesa."""
        sel = self.selected()
        if not sel:
            return {"connected": False, "on": False, "status": _("Set up the lamp"), "colour": True, "white": True}
        if sel != core.ALL:
            return dict(self.lamp_states.get(sel, {"connected": False, "status": _("Connecting…")}))
        states = [self.lamp_states.get(c["id"], {}) for c in self.cfg["lamps"]]
        live = [s for s in states if s.get("connected")]
        lit = [s for s in live if s.get("on")]
        # referencia para brilho/cor: a primeira acesa (o painel mostra a luz que se ve)
        view = dict(lit[0] if lit else live[0] if live else (states[0] or {"connected": False}))
        view.update(connected=bool(live), on=any(s.get("on") for s in live), count=len(states),
                    countOn=sum(1 for s in live if s.get("on")), countLive=len(live),
                    colour=any(s.get("colour", True) for s in live) if live else True,
                    white=any(s.get("white", True) for s in live) if live else True)
        return view

    # ----- estado -----
    def on_state(self, state):
        # pode vir de qualquer thread: o sinal leva para a thread da interface (Backend._lamp_state)
        self.backend._incoming.emit(state)

    def update_tray(self):
        if not self.tray:
            return
        st = self.view_state()
        rgb = None
        if st.get("connected") and st.get("on"):
            rgb = core.hsv_rgb(st["h"], st["s"]) if st.get("mode") == "colour" else core.kelvin_rgb(st.get("temp", 50))
        if rgb != self.tray_rgb:  # recriar o icone a cada arrasto do slider e caro
            self.tray_rgb = rgb
            self.tray.setIcon(pil_to_icon(tray_icon(rgb)))
        lines = []
        for c in self.cfg["lamps"]:
            s = self.lamp_states.get(c["id"], {})
            status = s.get("status") or (_("on") if s.get("on") else _("off"))
            lines.append(f"{self.lamp_name(c)}: {status}")
        self.tray.setToolTip(("Lume\n" + "\n".join(lines))[:127])

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
        for lamp in list(self.lamps.values()):
            threading.Thread(target=lamp.refresh, daemon=True).start()

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
    if core.FROZEN:
        # sem console no executavel: erros vao para lume.log, ao lado da configuracao
        os.makedirs(core.config_dir(), exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(core.config_dir(), "lume.log"), "w", encoding="utf-8", buffering=1)
    sys.exit(App().run())
