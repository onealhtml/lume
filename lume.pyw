"""Lume: controle de lampada Tuya (Positivo, Smart Life, etc.) pela rede local.
Fica na bandeja do Windows: clique no icone abre o painel rapido, botao direito abre o menu."""
import ctypes
import json
import os
import sys
import threading
import time
import winreg

import pystray
import tinytuya
import webview
import smartlife_qr
from icons import tray_icon

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
UI_PATH = os.path.join(APP_DIR, "ui", "index.html")
DEFAULT_CONFIG = {"id": "", "ip": "Auto", "key": "", "version": "3.5", "name": "Lâmpada", "last_ip": ""}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Lume"
OLD_RUN_NAMES = ("LampadaTuya",)
ICON_PATH = os.path.join(APP_DIR, "assets", "lume.ico")

# tamanho do painel (CSS px); bordas arredondadas e sombra vem do proprio Windows (DWM)
PANEL_W, PANEL_H, MARGIN = 340, 470, 0


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(enable):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        for old in OLD_RUN_NAMES:  # entrada de quando o app ainda nao se chamava Lume
            try:
                winreg.DeleteValue(k, old)
            except OSError:
                pass
        if enable:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{pythonw}" "{os.path.abspath(__file__)}"')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


def work_area():
    rect = (ctypes.c_long * 4)()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, rect, 0)  # SPI_GETWORKAREA
    return tuple(rect)


def apply_glass(hwnd):
    """Vidro acrilico nativo do Windows 11 atras da pagina, cantos arredondados e sombra."""
    dwm = ctypes.windll.dwmapi
    margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
    dwm.DwmExtendFrameIntoClientArea(hwnd, margins)
    for attr, value in ((20, 1),   # DWMWA_USE_IMMERSIVE_DARK_MODE
                        (33, 2),   # DWMWA_WINDOW_CORNER_PREFERENCE = arredondado
                        (38, 3)):  # DWMWA_SYSTEMBACKDROP_TYPE = acrilico
        v = ctypes.c_int(value)
        dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))


def force_foreground(hwnd):
    """SetForegroundWindow sozinho e bloqueado quando outro processo tem o foco;
    ligar a fila de entrada a da janela em primeiro plano libera a troca."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = kernel32.GetCurrentThreadId()
    attached = fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(me, fg_thread, False)


def hide_from_taskbar(hwnd):
    """Janela de ferramenta: sem botao na barra de tarefas nem no Alt+Tab, como um menu da bandeja."""
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)


def kelvin_rgb(temp_pct):
    # aproximacao: 0% = 2700K ambar, 100% = 6500K azulado
    warm, cool = (255, 177, 92), (214, 228, 255)
    t = temp_pct / 100
    return tuple(round(w + (c - w) * t) for w, c in zip(warm, cool))


def hsv_rgb(h, s):
    import colorsys
    return tuple(round(x * 255) for x in colorsys.hsv_to_rgb(h / 360, s / 100, 1))


class Lamp:
    """Conexao com a lampada. Comandos sao agrupados: so o ultimo valor de cada DP e enviado.
    Se a lampada some (interruptor, Wi-Fi, IP novo), reconecta sozinha em segundo plano."""

    def __init__(self, get_cfg, on_change):
        self.get_cfg = get_cfg  # sempre le a config atual do App, nunca uma copia antiga
        self.on_change = on_change
        self.dev = None
        self.state = {"connected": False, "on": False, "mode": "white", "bright": 100,
                      "temp": 50, "h": 30, "s": 100, "status": "Desconectado"}
        self._pending = {}
        self._last_send = 0.0
        self._gen = 0              # cada connect() invalida as tentativas anteriores
        self._cv = threading.Condition()  # protege state + _pending
        self._io = threading.Lock()       # uma conversa com a lampada por vez
        threading.Thread(target=self._worker, daemon=True).start()

    # ----- conexao -----
    def connect(self):
        with self._cv:
            self._gen += 1
            gen = self._gen
            self.dev = None
        cfg = self.get_cfg()
        if not cfg["id"] or not cfg["key"]:
            self._update(connected=False, status="Configure a lâmpada")
            return
        self._update(connected=False, status="Conectando…")
        threading.Thread(target=self._connect_loop, args=(gen,), daemon=True).start()

    def _connect_loop(self, gen):
        delay = 3
        while gen == self._gen:
            err = self._try_connect(gen)
            if err is None or gen != self._gen:
                return
            self._update(connected=False, status=err)
            time.sleep(delay)
            delay = min(delay * 2, 30)

    def _try_connect(self, gen):
        """None se conectou; senao a mensagem de erro."""
        cfg = self.get_cfg()
        ip = cfg["ip"] if cfg["ip"] not in ("", "Auto") else cfg.get("last_ip", "")
        try:
            st, dev = self._open(cfg, ip) if ip else (None, None)
            if not (st and "dps" in st) and cfg["ip"] in ("", "Auto"):
                # IP antigo nao respondeu: procura a lampada pelo ID na rede
                found = tinytuya.find_device(cfg["id"])
                if found and found.get("ip"):
                    st, dev = self._open(cfg, found["ip"])
        except Exception as e:  # noqa: BLE001
            return f"Erro: {e}"[:120]
        if not (st and "dps" in st):
            return "Lâmpada não respondeu. Ela está ligada no interruptor?"
        with self._cv:
            if gen != self._gen:
                return None
            self.dev = dev
            self._parse(st["dps"])
        if cfg["ip"] in ("", "Auto") and cfg.get("last_ip") != dev.address:
            cfg["last_ip"] = dev.address
            save_config(cfg)
        self._update(connected=True, status="")
        return None

    def _open(self, cfg, ip):
        dev = tinytuya.BulbDevice(cfg["id"], ip, cfg["key"], version=float(cfg["version"]))
        dev.set_socketPersistent(True)
        dev.set_socketTimeout(2)
        dev.set_socketRetryLimit(1)
        dev.set_socketRetryDelay(0.5)
        with self._io:
            return dev.status(), dev

    def _lost(self, dev, err):
        """Chamado quando um comando falha: marca desconectada e tenta de novo."""
        with self._cv:
            if dev is not self.dev:
                return  # ja estamos reconectando
        self._update(connected=False, status=f"Conexão perdida ({err}). Reconectando…"[:120])
        self.connect()

    def refresh(self):
        dev = self.dev
        if not dev:
            if not self.state["connected"]:
                self.connect()
            return
        with self._io:
            try:
                st = dev.status()
            except Exception:  # noqa: BLE001
                st = None
        if not (st and "dps" in st):
            self._lost(dev, (st or {}).get("Error", "sem resposta"))
            return
        with self._cv:
            # nao sobrescreve o que o usuario acabou de mudar e ainda nao chegou na lampada
            if self._pending or time.time() - self._last_send < 1.5:
                return
            self._parse(st["dps"])
        self._update()

    def _parse(self, dps):
        s = self.state
        s["on"] = bool(dps.get("20", s["on"]))
        s["mode"] = dps.get("21", s["mode"])
        if "23" in dps:
            s["temp"] = round(dps["23"] / 10)
        colour = dps.get("24")
        if colour and len(colour) == 12:
            s["h"], s["s"] = int(colour[0:4], 16), round(int(colour[4:8], 16) / 10)
        if s["mode"] == "colour" and colour:
            s["bright"] = max(1, round(int(colour[8:12], 16) / 10))
        elif "22" in dps:
            s["bright"] = max(1, round(dps["22"] / 10))

    def _update(self, **kw):
        with self._cv:
            self.state.update(kw)
            snapshot = dict(self.state)
        self.on_change(snapshot)

    # ----- comandos -----
    def _command(self, changes, dps_fn):
        """Atualiza o estado e enfileira os DPs na mesma secao critica (mantem a ordem)."""
        with self._cv:
            if not self.dev:
                return False
            self.state.update(changes)
            self._pending.update(dps_fn(self.state))
            self._cv.notify()
            snapshot = dict(self.state)
        self.on_change(snapshot)
        return True

    def _worker(self):
        while True:
            with self._cv:
                while not self._pending:
                    self._cv.wait()
                dps, self._pending = self._pending, {}
                dev = self.dev
                self._last_send = time.time()
            if not dev:
                continue
            with self._io:
                try:
                    r = dev.set_multiple_values(dps)
                except Exception as e:  # noqa: BLE001
                    r = {"Error": str(e)}
            if isinstance(r, dict) and "Error" in r:
                self._lost(dev, r["Error"])
            time.sleep(0.05)

    @staticmethod
    def _colour_hex(s):
        return f"{s['h']:04x}{s['s'] * 10:04x}{s['bright'] * 10:04x}"

    def power(self, on):
        return self._command({"on": bool(on)}, lambda s: {"20": s["on"]})

    def brightness(self, pct):
        pct = max(1, min(100, int(pct)))
        # cena/musica nao tem brilho proprio: volta para o branco
        mode = "colour" if self.state["mode"] == "colour" else "white"
        return self._command({"on": True, "bright": pct, "mode": mode}, lambda s: (
            {"20": True, "24": self._colour_hex(s)} if mode == "colour"
            else {"20": True, "21": "white", "22": max(10, pct * 10)}))

    def temperature(self, pct):
        pct = max(0, min(100, int(pct)))
        return self._command({"on": True, "mode": "white", "temp": pct}, lambda s: (
            {"20": True, "21": "white", "22": max(10, s["bright"] * 10), "23": pct * 10}))

    def colour(self, h, s):
        return self._command({"on": True, "mode": "colour", "h": int(h) % 360, "s": max(0, min(100, int(s)))},
                             lambda st: {"20": True, "21": "colour", "24": self._colour_hex(st)})

    def tray_rgb(self):
        s = self.state
        if not (s["connected"] and s["on"]):
            return None
        return hsv_rgb(s["h"], s["s"]) if s["mode"] == "colour" else kelvin_rgb(s["temp"])


class Api:
    """Metodos chamados pelo JavaScript do painel (window.pywebview.api.*)."""

    def __init__(self, app):
        self._app = app

    def get_state(self):
        return self._app.lamp.state

    # retornam False quando a lampada nao esta conectada (o painel avisa e ressincroniza)
    def power(self, on):
        return self._app.lamp.power(on)

    def brightness(self, pct):
        return self._app.lamp.brightness(pct)

    def temperature(self, pct):
        return self._app.lamp.temperature(pct)

    def colour(self, h, s):
        return self._app.lamp.colour(h, s)

    def hide(self):
        self._app.hide_panel()

    def get_config(self):
        return {**self._app.cfg, "autostart": autostart_enabled()}

    def save_config(self, cfg):
        old = self._app.cfg
        new = {k: str(cfg.get(k, DEFAULT_CONFIG[k])).strip() for k in DEFAULT_CONFIG}
        new["last_ip"] = old.get("last_ip", "") if new["id"] == old["id"] else ""
        self._app.cfg = new
        save_config(new)
        self._app.lamp.connect()

    def set_autostart(self, enable):
        try:
            set_autostart(bool(enable))
        except OSError:
            pass
        return autostart_enabled()

    def scan(self):
        try:
            found = tinytuya.deviceScan(verbose=False, maxretry=15)
        except Exception:  # noqa: BLE001 - porta UDP ocupada etc.
            return []
        return [{"ip": ip, "id": d.get("gwId", ""), "version": d.get("version", "")} for ip, d in found.items()]

    def smartlife_start(self, user_code):
        try:
            return smartlife_qr.start(user_code.strip())
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def smartlife_poll(self, token, user_code):
        try:
            return smartlife_qr.poll(token, user_code.strip())
        except Exception as e:  # noqa: BLE001
            return {"error": f"Falha no login: {e}"}

    def quit(self):
        self._app.quit()


class App:
    def __init__(self):
        self.cfg = load_config()
        self.visible = False
        self.hidden_at = 0.0
        self.shown_at = 0.0
        self.glass_applied = False
        self.quitting = False
        self.tray_rgb = "unset"
        self.state_lock = threading.Lock()
        self.lamp = Lamp(lambda: self.cfg, self.on_state)
        self.window = webview.create_window(
            "Lume", UI_PATH, js_api=Api(self), width=PANEL_W + 2 * MARGIN, height=PANEL_H + 2 * MARGIN,
            frameless=True, transparent=True, on_top=True, hidden=True, resizable=False, shadow=False,
            easy_drag=False, background_color="#000000",
        )
        self.tray = pystray.Icon("lume", tray_icon(), "Lume", pystray.Menu(
            pystray.MenuItem("Abrir", lambda: self.toggle_panel(), default=True),
            pystray.MenuItem(lambda _: "Desligar" if self.lamp.state["on"] else "Ligar",
                             lambda: self.lamp.power(not self.lamp.state["on"])),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda: self.quit()),
        ))

    def run(self):
        self.tray.run_detached()
        self.window.events.closing += self._on_closing
        try:
            webview.start(self._started, icon=ICON_PATH)
        finally:
            self.tray.stop()

    def _on_closing(self):
        # Alt+F4 no painel so esconde; fechar de verdade e pelo menu Sair
        if self.quitting:
            return True
        self.hide_panel()
        return False

    def _started(self):
        self.lamp.connect()
        if not self.cfg["key"]:
            self.show_panel(view="settings")

    def on_state(self, state):
        # chamado de varias threads ao mesmo tempo (cada chamada do JS roda numa thread)
        with self.state_lock:
            try:
                rgb = self.lamp.tray_rgb()
                if rgb != self.tray_rgb:  # recriar o icone a cada arrasto do slider e caro
                    self.tray_rgb = rgb
                    self.tray.icon = tray_icon(rgb)
                status = state["status"] or ("acesa" if state["on"] else "apagada")
                self.tray.title = f"Lume – {self.cfg['name']} {status}"[:127]  # limite do Windows
                self.tray.update_menu()
            except Exception:  # noqa: BLE001 - o icone nao pode derrubar o controle da lampada
                pass
        if self.visible:
            self.window.evaluate_js(f"window.onState && onState({json.dumps(state)})")

    # ----- painel -----
    def toggle_panel(self):
        if self.visible:
            self.hide_panel()
        elif time.time() - self.hidden_at > 0.4:  # o clique no icone acabou de fechar o painel
            self.show_panel()

    def show_panel(self, view="main"):
        # abre perto do cursor (o icone da bandeja), sem sair da area de trabalho;
        # a barra de tarefas pode estar em qualquer lado da tela
        scale = ctypes.windll.user32.GetDpiForSystem() / 96
        left, top, right, bottom = work_area()
        cursor = (ctypes.c_long * 2)()
        ctypes.windll.user32.GetCursorPos(cursor)
        w, h = round((PANEL_W + 2 * MARGIN) * scale), round((PANEL_H + 2 * MARGIN) * scale)
        edge = round((MARGIN - 12) * scale)  # painel a 12px da borda da area de trabalho
        cx, cy = cursor
        gap = round(16 * scale)
        if left <= cx < right and top <= cy < bottom:
            # icone dentro da area de trabalho (bandeja estendida, barra de terceiros):
            # abre ao lado do cursor para nao cobrir o icone
            x = cx + gap if cx < (left + right) / 2 else cx - w - gap
            y = cy - h + gap if cy > (top + bottom) / 2 else cy - gap
        else:
            # icone na barra de tarefas: o limite da area de trabalho ja deixa o painel colado nela
            x, y = cx - w // 2, cy - h // 2
        x = min(max(x, left - edge), right - w + edge)
        y = min(max(y, top - edge), bottom - h + edge)
        self.visible = True
        # resize/move do pywebview usam pixels logicos e rodam na thread da janela
        self.window.resize(PANEL_W + 2 * MARGIN, PANEL_H + 2 * MARGIN)
        self.window.move(round(x / scale), round(y / scale))
        if not self.glass_applied:
            hide_from_taskbar(self.window.native.Handle.ToInt32())  # antes de mostrar, senao o botao fica
        self.window.show()
        if not self.glass_applied:
            from System.Drawing import Color  # disponivel depois que o pywebview carrega o .NET
            self.window.native.BackColor = Color.Black  # preto = area onde o acrilico aparece
            apply_glass(self.window.native.Handle.ToInt32())
            self.glass_applied = True
        self.window.evaluate_js(f"window.onShow && onShow({json.dumps(self.lamp.state)}, '{view}')")
        self.shown_at = time.time()
        threading.Thread(target=self._watch_focus, args=(self.shown_at,), daemon=True).start()
        threading.Thread(target=self.lamp.refresh, daemon=True).start()

    def _watch_focus(self, shown_at):
        """Fecha o painel quando outra janela ganha o foco (clique fora).
        O evento blur do JS nao serve: o clique no icone devolve o foco a barra de tarefas
        logo depois do painel abrir, e ele fechava na hora."""
        user32 = ctypes.windll.user32
        hwnd = self.window.native.Handle.ToInt32()
        had_focus, baseline = False, None
        while self.visible and self.shown_at == shown_at:
            fg = user32.GetForegroundWindow()
            if fg == hwnd:
                had_focus = True
            elif had_focus or (baseline is not None and fg != baseline):
                self.hide_panel()
                return
            elif time.time() - shown_at < 1.5:
                force_foreground(hwnd)
            elif baseline is None:  # o Windows nao deixou pegar o foco; fecha quando o foco mudar
                baseline = fg
            time.sleep(0.08)

    def hide_panel(self):
        if self.visible:
            self.visible = False
            self.hidden_at = time.time()
            self.window.hide()

    def quit(self):
        self.quitting = True
        self.tray.stop()
        self.window.destroy()


if __name__ == "__main__":
    # identidade propria no Windows, em vez de aparecer como "Python"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Lume")
    App().run()
