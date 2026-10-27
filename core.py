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

from i18n import _

FROZEN = getattr(sys, "frozen", False)  # rodando do executavel gerado pelo PyInstaller
# arquivos do app (ui/, assets/): no executavel ficam na pasta temporaria/interna do PyInstaller
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = {"lamps": [], "selected": "", "glass": "acrylic"}  # vidro no Windows: "acrylic" ou "blur"
LAMP_DEFAULTS = {"id": "", "key": "", "name": "", "ip": "Auto", "last_ip": "", "version": "3.5"}
ALL = "all"  # "selected" quando o painel controla todas as lampadas juntas


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
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    # ate a 1.1 havia uma lampada so, com os campos na raiz
    if raw.get("id") and not isinstance(raw.get("lamps"), list):
        raw["lamps"] = [{k: raw[k] for k in LAMP_DEFAULTS if k in raw}]
    cfg = {k: raw.get(k, v) for k, v in DEFAULT_CONFIG.items()}
    lamps = cfg["lamps"] if isinstance(cfg["lamps"], list) else []
    cfg["lamps"] = [{**LAMP_DEFAULTS, **{k: str(v) for k, v in lamp.items() if k in LAMP_DEFAULTS}}
                    for lamp in lamps if isinstance(lamp, dict) and lamp.get("id")]
    for k in ("selected", "glass"):
        cfg[k] = str(cfg[k]) if cfg[k] is not None else DEFAULT_CONFIG[k]
    return cfg


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
        with open(path, "w", encoding="utf-8") as f:
            f.write(_desktop_entry("X-GNOME-Autostart-enabled=true\n"))


# ----- atalho no menu (Linux) -----
DESKTOP_ID = "lume"  # no Wayland o app_id da janela; o GNOME acha o app pelo .desktop com esse nome
_DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
_MENU_DESKTOP = os.path.join(_DATA_HOME, "applications", DESKTOP_ID + ".desktop")
_MENU_ICON = os.path.join(_DATA_HOME, "icons", "hicolor", "256x256", "apps", DESKTOP_ID + ".png")


def _desktop_entry(extra=""):
    # no executavel o icone e copiado para fora (o assets/ do AppImage muda de lugar a cada execucao);
    # caminho completo e nao o nome: o GNOME so ve uma pasta de icones nova depois de reiniciar a sessao
    icon = _MENU_ICON if FROZEN else os.path.join(APP_DIR, "assets", "lume.png")
    exec_line = " ".join(f'"{c}"' for c in _launch_cmd())
    return ("[Desktop Entry]\nType=Application\nName=Lume\nComment=Control a Tuya lamp over the local network\n"
            f"Exec={exec_line}\nIcon={icon}\nCategories=Utility;\n{extra}")


def install_desktop_entry():
    """Linux, so no executavel: .desktop e icone do usuario. Sem eles o GNOME atribui a janela a quem
    abriu o Lume (o Arquivos, um terminal...), e quando esse app fecha a doca se perde e o shell cai."""
    if not FROZEN or sys.platform in ("win32", "darwin"):
        return
    try:
        src = os.path.join(APP_DIR, "assets", "lume.png")
        if not os.path.exists(_MENU_ICON) or os.path.getsize(_MENU_ICON) != os.path.getsize(src):
            os.makedirs(os.path.dirname(_MENU_ICON), exist_ok=True)
            shutil.copyfile(src, _MENU_ICON)
        entry = _desktop_entry()
        try:
            with open(_MENU_DESKTOP, encoding="utf-8") as f:
                current = f.read()
        except OSError:
            current = ""
        if current != entry:  # primeira vez, ou o .AppImage mudou de pasta
            os.makedirs(os.path.dirname(_MENU_DESKTOP), exist_ok=True)
            with open(_MENU_DESKTOP, "w", encoding="utf-8") as f:
                f.write(entry)
            if autostart_enabled():  # o inicio automatico apontava para o caminho antigo
                set_autostart(True)
    except OSError:
        pass


# ----- cores -----
def kelvin_rgb(temp_pct):
    # aproximacao: 0% = 2700K ambar, 100% = 6500K azulado
    warm, cool = (255, 177, 92), (214, 228, 255)
    t = temp_pct / 100
    return tuple(round(w + (c - w) * t) for w, c in zip(warm, cool))


def hsv_rgb(h, s):
    return tuple(round(x * 255) for x in colorsys.hsv_to_rgb(h / 360, s / 100, 1))


class Scheme:
    """Onde cada lampada guarda cada coisa. Modelos Tuya usam tres arranjos de DPs (os tipos A, B e C
    da tinytuya); o tipo e descoberto pelo status na primeira conexao."""

    def __init__(self, kind, switch, mode, bright, temp, colour, bmin, bmax, tmax):
        self.kind, self.switch, self.mode, self.bright, self.temp, self.colour = kind, switch, mode, bright, temp, colour
        self.bmin, self.bmax, self.tmax = bmin, bmax, tmax

    @staticmethod
    def detect(dps):
        """Como a tinytuya: o tipo pelo DP de ligar, e so os DPs que aparecem no status (lampada so de
        branco nao tem cor; dimmer tipo C sem DP 3 nao tem temperatura)."""
        if "20" in dps and "1" not in dps:
            base = SCHEMES["B"]
        elif "1" in dps and "20" not in dps:
            base = SCHEMES["A" if isinstance(dps.get("2"), str) else "C"]
        else:  # nao parece lampada: so liga e desliga
            return Scheme("switch", "1" if "1" in dps else "20", None, None, None, None, 25, 255, 255)

        def present(dp):
            return dp if dp and dp in dps else None
        return Scheme(base.kind, base.switch, present(base.mode), present(base.bright), present(base.temp),
                      present(base.colour), base.bmin, base.bmax, base.tmax)

    # brilho e temperatura em % <-> valor da lampada
    def bright_raw(self, pct):
        return max(self.bmin, round(pct / 100 * self.bmax))

    def bright_pct(self, raw):
        return max(1, min(100, round(raw / self.bmax * 100)))

    def temp_raw(self, pct):
        return round(pct / 100 * self.tmax)

    def temp_pct(self, raw):
        return max(0, min(100, round(raw / self.tmax * 100)))

    # cor: matiz 0-359, saturacao e brilho 0-100
    def colour_raw(self, h, s, v):
        if self.kind == "B":  # hhhhssssvvvv, s e v de 0 a 1000
            return f"{h:04x}{s * 10:04x}{v * 10:04x}"
        r, g, b = (round(x * 255) for x in colorsys.hsv_to_rgb(h / 360, s / 100, v / 100))
        return tinytuya.BulbDevice.rgb_to_hexvalue(r, g, b, "rgb8")  # rrggbbhhhhssvv

    def colour_hsv(self, raw):
        """(h, s, v) ou None se o valor nao e reconhecido."""
        try:
            h, s, v = tinytuya.BulbDevice.hexvalue_to_hsv(raw, "hsv16" if self.kind == "B" else "rgb8")
        except (ValueError, TypeError, IndexError):
            return None
        return round(h * 360) % 360, round(s * 100), max(1, round(v * 100))


SCHEMES = {
    "A": Scheme("A", "1", "2", "3", "4", "5", 25, 255, 255),
    "B": Scheme("B", "20", "21", "22", "23", "24", 10, 1000, 1000),
    "C": Scheme("C", "1", None, "2", "3", None, 25, 255, 255),  # so branco: sem modo e sem cor
}


class Lamp:
    """Conexao com uma lampada. Comandos sao agrupados: so o ultimo valor de cada DP e enviado.
    Se a lampada some (interruptor, Wi-Fi, IP novo), reconecta sozinha em segundo plano."""

    def __init__(self, get_cfg, on_change, save):
        self.get_cfg = get_cfg  # sempre le a config atual desta lampada, nunca uma copia antiga
        self.on_change = on_change
        self.save = save        # grava a configuracao (o IP descoberto fica para a proxima vez)
        self.id = get_cfg()["id"]
        self.dev = None
        self.scheme = SCHEMES["B"]
        self.state = {"id": self.id, "connected": False, "on": False, "mode": "white", "bright": 100,
                      "temp": 50, "h": 30, "s": 100, "colour": True, "white": True, "status": _("Disconnected")}
        self._pending = {}
        self._last_send = 0.0
        self._known = {}  # ultimo valor de cada DP que a lampada confirmou (para so mandar o que mudou)
        self._gen = 0              # cada connect() invalida as tentativas anteriores
        self._closed = False
        self._cv = threading.Condition()  # protege state + _pending
        self._io = threading.Lock()       # uma conversa com a lampada por vez
        threading.Thread(target=self._worker, daemon=True).start()

    # ----- conexao -----
    def connect(self):
        with self._cv:
            if self._closed:
                return
            self._gen += 1
            gen = self._gen
            self.dev = None
        cfg = self.get_cfg()
        if not cfg["id"] or not cfg["key"]:
            self._update(connected=False, status=_("Set up the lamp"))
            return
        self._update(connected=False, status=_("Connecting…"))
        threading.Thread(target=self._connect_loop, args=(gen,), daemon=True).start()

    def close(self):
        """Lampada removida: para de reconectar e solta a conexao."""
        with self._cv:
            self._gen += 1
            self._closed = True
            dev, self.dev = self.dev, None
            self._pending = {}
            self._cv.notify()
        if dev:
            try:
                dev.close()
            except Exception:  # noqa: BLE001
                pass

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
            return (_("Error: %s") % e)[:120]
        if not (st and "dps" in st):
            return _("The lamp didn't answer. Is it switched on at the wall?")
        with self._cv:
            if gen != self._gen:
                stale = dev
            else:
                stale = None
                self.dev = dev
                self.scheme = Scheme.detect(st["dps"])
                self.state["colour"] = self.scheme.colour is not None
                self.state["white"] = self.scheme.temp is not None
        if stale:  # outra tentativa (ou close) passou na frente: solta este socket
            try:
                stale.close()
            except Exception:  # noqa: BLE001
                pass
            return None
        with self._cv:
            self._known = {}
            self._parse(st["dps"])
        if cfg["ip"] in ("", "Auto") and cfg.get("last_ip") != dev.address:
            cfg["last_ip"] = dev.address
            self.save()
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
            if dev is not self.dev or self._closed:
                return  # ja estamos reconectando, ou a lampada foi removida
        self._update(connected=False, status=(_("Connection lost (%s). Reconnecting…") % err)[:120])
        self.connect()

    def refresh(self):
        dev = self.dev
        if not dev:
            if not self.state["connected"] and not self._closed:
                self.connect()
            return
        with self._io:
            try:
                st = dev.status()
            except Exception:  # noqa: BLE001
                st = None
        if not (st and "dps" in st):
            self._lost(dev, (st or {}).get("Error", _("no response")))
            return
        with self._cv:
            # nao sobrescreve o que o usuario acabou de mudar e ainda nao chegou na lampada
            if self._pending or time.time() - self._last_send < 1.5:
                return
            self._parse(st["dps"])
        self._update()

    def _parse(self, dps):
        self._known.update(dps)
        sc, s = self.scheme, self.state
        s["on"] = bool(dps.get(sc.switch, s["on"]))
        if sc.mode:
            mode = str(dps.get(sc.mode, s["mode"]))
            # tipo A usa "colour"/"white"/"scene_1"...; o painel so precisa de cor x branco
            s["mode"] = "colour" if mode.startswith("colour") else "white" if mode.startswith("white") else mode
        if sc.temp and sc.temp in dps:
            s["temp"] = sc.temp_pct(dps[sc.temp])
        hsv = sc.colour_hsv(dps[sc.colour]) if sc.colour and dps.get(sc.colour) else None
        if hsv:
            s["h"], s["s"] = hsv[0], hsv[1]
        if s["mode"] == "colour" and hsv:
            s["bright"] = hsv[2]
        elif sc.bright and sc.bright in dps:
            s["bright"] = sc.bright_pct(dps[sc.bright])

    def _update(self, **kw):
        with self._cv:
            if self._closed:
                return
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
            self._pending.update({k: v for k, v in dps_fn(self.scheme, self.state).items() if k})
            self._cv.notify()
            snapshot = dict(self.state)
        self.on_change(snapshot)
        return True

    def _worker(self):
        while True:
            with self._cv:
                while not self._pending and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
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

    def power(self, on):
        return self._command({"on": bool(on)}, lambda sc, s: {sc.switch: s["on"]})

    def brightness(self, pct):
        pct = max(1, min(100, int(pct)))
        # cena/musica nao tem brilho proprio: volta para o branco
        mode = "colour" if self.state["mode"] == "colour" and self.scheme.colour else "white"

        def dps(sc, s):
            if mode == "colour":
                return {sc.switch: True, sc.colour: sc.colour_raw(s["h"], s["s"], pct)}
            d = {sc.switch: True, sc.bright: sc.bright_raw(pct) if sc.bright else None}
            if sc.mode:
                d[sc.mode] = "white"
            return d
        return self._command({"on": True, "bright": pct, "mode": mode}, dps)

    def temperature(self, pct):
        pct = max(0, min(100, int(pct)))

        def dps(sc, s):
            # brilho e temperatura juntos: mandar so a temperatura deixa algumas lampadas no brilho antigo
            d = {sc.switch: True, sc.bright: sc.bright_raw(s["bright"]) if sc.bright else None,
                 sc.temp: sc.temp_raw(pct) if sc.temp else None}
            if sc.mode:
                d[sc.mode] = "white"
            return d
        return self._command({"on": True, "mode": "white", "temp": pct}, dps)

    def colour(self, h, s):
        if not self.scheme.colour:  # lampada so de branco (tipo C): ignora, sem erro (modo "Todas")
            return self.dev is not None
        return self._command({"on": True, "mode": "colour", "h": int(h) % 360, "s": max(0, min(100, int(s)))},
                             lambda sc, st: {sc.switch: True, sc.mode: "colour",
                                             sc.colour: sc.colour_raw(st["h"], st["s"], st["bright"])})

    def tray_rgb(self):
        s = self.state
        if not (s["connected"] and s["on"]):
            return None
        return hsv_rgb(s["h"], s["s"]) if s["mode"] == "colour" else kelvin_rgb(s["temp"])
