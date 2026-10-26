"""Parte do Lume que nao depende de interface: configuracao, inicio automatico e a conexao com a lampada."""
import colorsys
import json
import os
import plistlib
import shutil
import sys
import threading
import time

import tinytuya

FROZEN = getattr(sys, "frozen", False)  # rodando do executavel gerado pelo PyInstaller
# arquivos do app (ui/, assets/): no executavel ficam na pasta temporaria/interna do PyInstaller
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = {"id": "", "ip": "Auto", "key": "", "version": "3.5", "name": "Lâmpada", "last_ip": "",
                  "glass": "acrylic"}  # vidro no Windows: "acrylic" ou "blur"


def config_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Lume")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Lume")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "lume")


CONFIG_PATH = os.path.join(config_dir(), "config.json")
LEGACY_CONFIG = os.path.join(APP_DIR, "config.json")  # versoes antigas guardavam ao lado do codigo


def load_config():
    if not os.path.exists(CONFIG_PATH) and os.path.exists(LEGACY_CONFIG):
        os.makedirs(config_dir(), exist_ok=True)
        shutil.move(LEGACY_CONFIG, CONFIG_PATH)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    os.makedirs(config_dir(), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ----- iniciar com o sistema -----
ENTRY = os.path.join(APP_DIR, "lume.pyw")
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WIN_OLD_NAMES = ("LampadaTuya",)  # de quando o app ainda nao se chamava Lume
_MAC_PLIST = os.path.expanduser("~/Library/LaunchAgents/ai.lume.tray.plist")
_LINUX_DESKTOP = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                              "autostart", "lume.desktop")


def _launch_cmd():
    if FROZEN:
        # AppImage: o executavel interno muda de lugar a cada execucao; o arquivo .AppImage nao
        return [os.environ.get("APPIMAGE") or sys.executable]
    exe = sys.executable
    if sys.platform == "win32":  # pythonw: sem janela de console
        exe = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return [exe, ENTRY]


def autostart_enabled():
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as k:
                winreg.QueryValueEx(k, "Lume")
                return True
        except OSError:
            return False
    return os.path.exists(_MAC_PLIST if sys.platform == "darwin" else _LINUX_DESKTOP)


def set_autostart(enable):
    cmd = _launch_cmd()
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            for name in _WIN_OLD_NAMES + (() if enable else ("Lume",)):
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass
            if enable:
                winreg.SetValueEx(k, "Lume", 0, winreg.REG_SZ, " ".join(f'"{c}"' for c in cmd))
        return
    path = _MAC_PLIST if sys.platform == "darwin" else _LINUX_DESKTOP
    if not enable:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if sys.platform == "darwin":
        with open(path, "wb") as f:
            plistlib.dump({"Label": "ai.lume.tray", "ProgramArguments": cmd, "RunAtLoad": True}, f)
    else:
        icon = os.path.join(APP_DIR, "assets", "lume.png")
        exec_line = " ".join(f'"{c}"' for c in cmd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Lume\n"
                    f"Exec={exec_line}\nIcon={icon}\nX-GNOME-Autostart-enabled=true\n")


# ----- cores -----
def kelvin_rgb(temp_pct):
    # aproximacao: 0% = 2700K ambar, 100% = 6500K azulado
    warm, cool = (255, 177, 92), (214, 228, 255)
    t = temp_pct / 100
    return tuple(round(w + (c - w) * t) for w, c in zip(warm, cool))


def hsv_rgb(h, s):
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
        self._known = {}  # ultimo valor de cada DP que a lampada confirmou (para so mandar o que mudou)
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
            self._known = {}
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
        self._known.update(dps)
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
                # so o que mudou: repetir "ligar" ou "modo cor" faz a lampada refazer a
                # transicao (fade) e a troca de cor parece atrasada
                dps = {k: v for k, v in dps.items() if self._known.get(k) != v}
            if not dev or not dps:
                continue
            with self._io:
                try:
                    r = dev.set_multiple_values(dps)
                except Exception as e:  # noqa: BLE001
                    r = {"Error": str(e)}
            if isinstance(r, dict) and "Error" in r:
                self._lost(dev, r["Error"])
            else:
                with self._cv:
                    self._known.update(dps)

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
