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
MUSIC_STYLES = ("pulse", "flow", "party")
MUSIC_DEFAULTS = {"style": "party", "sensitivity": 50, "dp": "27"}  # dp: "28" se a lampada ignora o 27
# vidro no Windows: "acrylic" ou "blur"
DEFAULT_CONFIG = {"lamps": [], "selected": "", "glass": "acrylic", "music": dict(MUSIC_DEFAULTS)}
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
    # sempre um dict novo: o padrao nao pode ser alterado por quem mexe em cfg["music"]
    m = cfg["music"] if isinstance(cfg["music"], dict) else {}
    try:
        sens = max(0, min(100, int(m.get("sensitivity", MUSIC_DEFAULTS["sensitivity"]))))
    except (TypeError, ValueError, OverflowError):
        sens = MUSIC_DEFAULTS["sensitivity"]
    cfg["music"] = dict(m, style=m.get("style") if m.get("style") in MUSIC_STYLES else MUSIC_DEFAULTS["style"],
                        sensitivity=sens, dp=str(m.get("dp")) if str(m.get("dp")) in ("27", "28") else "27")
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


def music_raw(h, s, v, jump=True, temp=0):
    """Quadro de tempo real dos DPs 27/28 (tipo B), 21 caracteres: M HHHH SSSS VVVV BBBB TTTT.
    M 0 = salto, 1 = gradiente; h 0-359, s e v 0-100 viram 0-1000. B (branco) fica 0: acima disso
    os LEDs brancos das lampadas RGBCW acendem junto. T so serve para o quadro nao repetir o anterior."""
    s = max(0, min(1000, int(round(s * 10))))
    v = max(0, min(1000, int(round(v * 10))))
    if 0 < v < 10:  # de 1 a 9 a lampada apaga; 10 e o minimo do produto
        v = 10
    return f"{0 if jump else 1}{int(round(h)) % 360:04x}{s:04x}{v:04x}{0:04x}{temp:04x}"


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


TIMEOUT = 2  # socket do caminho normal (o _open e a volta depois de cada quadro de musica)
# musica: intervalo minimo entre quadros por lampada. Tipo B (DP 27/28, tempo real): o quadro de golpe (flash,
# cor nova) sai 60 ms depois do anterior; os outros (o brilho respirando) a cada 0,25 s, para a lampada estar
# livre quando o golpe chega. Tres quadros seguidos nunca em menos de 0,2 s: na media no maximo 10/s (mais
# rapido que isso a lampada se perde). Tipo A (so o DP de cor): cada escrita faz fade, relatorio e talvez gravacao
MUSIC_GAP_BEAT = 0.06
MUSIC_GAP_LIVE = 0.25
MUSIC_GAP_PAIR = 0.2
MUSIC_GAP_COLOUR = 0.5
MUSIC_DEBUG = os.environ.get("LUME_MUSIC_DEBUG") == "1"  # uma linha por quadro de golpe (fila, envio, ACK)
MUSIC_KEEPALIVE = 8.0  # sem quadro por esse tempo: repete o ultimo (a lampada derruba conexao parada)
MUSIC_FAILS = 3  # quadros seguidos com erro: a musica acaba nesta lampada


class Lamp:
    """Conexao com uma lampada. Comandos sao agrupados: so o ultimo valor de cada DP e enviado.
    Se a lampada some (interruptor, Wi-Fi, IP novo), reconecta sozinha em segundo plano.
    No modo musica o worker manda so o quadro mais recente, no ritmo que a lampada aguenta."""

    def __init__(self, get_cfg, on_change, save):
        self.get_cfg = get_cfg  # sempre le a config atual desta lampada, nunca uma copia antiga
        self.on_change = on_change
        self.save = save        # grava a configuracao (o IP descoberto fica para a proxima vez)
        self.id = get_cfg()["id"]
        self.dev = None
        self.scheme = SCHEMES["B"]
        self.state = {"id": self.id, "connected": False, "on": False, "mode": "white", "bright": 100,
                      "temp": 50, "h": 30, "s": 100, "colour": True, "white": True, "music": False,
                      "status": _("Disconnected")}
        self._pending = {}
        self._force = set()  # DPs que vao mesmo iguais ao _known (a restauracao depois da musica)
        self._last_send = 0.0
        self._known = {}  # ultimo valor de cada DP que a lampada confirmou (para so mandar o que mudou)
        self._busy = False         # o worker esta conversando com a lampada (wait_idle)
        self._music = None         # sessao de musica: dp, live (DP 27/28 de tempo real), mode, gap, enter
        self._saved = None         # como sair da musica (DPs e estado de antes); fica ate a lampada confirmar
        self._frame = None         # quadro (h, s, v, jump) ainda nao mandado: so o mais recente importa
        self._beat = None          # o quadro guardado e de golpe: quando entrou na fila (passa na frente)
        self._sent2 = 0.0          # envio anterior ao ultimo (media de 10/s)
        self._last_frame = None    # ultimo quadro mandado, e a string dele (keepalive)
        self._last_raw = None
        self._sent = 0.0           # time.monotonic() do ultimo quadro ou keepalive
        self._fresh = False        # o proximo quadro e o primeiro depois de entrar na musica
        self._t = 0                # T do quadro, alterna a cada entrada: a lampada ignora quadro repetido
        self._music_fails = 0      # quadros seguidos que falharam (mesmo reconectando entre eles)
        self._first = True         # a primeira conexao deste objeto (pode achar a lampada presa na musica)
        self._gen = 0              # cada connect() invalida as tentativas anteriores
        self._closed = False
        self._cv = threading.Condition()  # protege state, _pending e a musica; nunca segurado durante I/O
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
            self._pending, self._force = {}, set()
            self._music = self._frame = self._saved = self._beat = None
            self.state["music"] = False
            self._cv.notify_all()
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
                self._known = {}
                self._parse(st["dps"])
                self._resume()
        if stale:  # outra tentativa (ou close) passou na frente: solta este socket
            try:
                stale.close()
            except Exception:  # noqa: BLE001
                pass
            return None
        if cfg["ip"] in ("", "Auto") and cfg.get("last_ip") != dev.address:
            cfg["last_ip"] = dev.address
            self.save()
        self._update(connected=True, status="")
        return None

    def _open(self, cfg, ip):
        dev = tinytuya.BulbDevice(cfg["id"], ip, cfg["key"], version=float(cfg["version"]))
        dev.set_socketPersistent(True)
        dev.set_socketTimeout(TIMEOUT)
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

    def _resume(self):
        """Com _cv, logo depois de (re)conectar."""
        sc = self.scheme
        if self._music:
            # a lampada pode ter reiniciado: entra de novo e repete o ultimo quadro (com o T trocado)
            self.state.update(on=True, mode="music")
            if self._frame is None:
                self._frame = self._last_frame
            self._enter()
        elif self._saved:
            # a saida da musica nao chegou na lampada (a conexao caiu antes): manda de novo
            self._pending.update(self._saved["dps"])
            self._force.update(self._saved["dps"])
            self.state.update(self._saved["state"])
            self._cv.notify_all()
        elif self._first and sc.mode and self._known.get(sc.mode) == "music":
            # Lume saiu sem devolver a cor (fechou reconectando, travou, sem energia): a lampada gravou
            # o modo musica e acende vermelha. So na primeira conexao: depois pode ser o app do celular.
            # So o modo, nunca o DP de ligar: abrir o Lume nao acende lampada apagada
            mode = "colour" if sc.colour else "white"
            self._pending[sc.mode] = mode
            self.state["mode"] = mode
            hsv = sc.colour_hsv(self._known.get(sc.colour)) if mode == "colour" and self._known.get(sc.colour) else None
            if hsv:
                self.state["bright"] = hsv[2]
            self._cv.notify_all()  # sem musica o worker espera sem prazo
        self._first = False

    def refresh(self):
        if self._music:  # na musica a lampada so recebe quadros: sem consultar o status
            return
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
            if self._music or self._pending or time.time() - self._last_send < 1.5:
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
        """Atualiza o estado e enfileira os DPs na mesma secao critica (mantem a ordem).
        `changes` pode ser uma funcao do estado (depois de sair da musica)."""
        with self._cv:
            if not self.dev:
                return False
            # comando durante a musica encerra ela antes, no mesmo pacote
            restore = self._end_music() if self._music else {}
            self.state.update(changes(self.state) if callable(changes) else changes)
            dps = {k: v for k, v in dps_fn(self.scheme, self.state).items() if k}
            if self.scheme.mode not in dps:  # o comando nao escolhe o modo: a restauracao tira da musica
                self._pending.update(restore)
                self._force.update(restore)
            self._pending.update(dps)
            self._cv.notify_all()
            snapshot = dict(self.state)
        self.on_change(snapshot)
        return True

    def _worker(self):
        while True:
            with self._cv:
                self._busy = False
                self._cv.notify_all()  # wait_idle
                job = None
                # primeiro os comandos normais; depois o quadro de musica quando o intervalo deixa
                while not self._closed and not self._pending:
                    job, wait = self._music_job()
                    if job:
                        break
                    self._cv.wait(wait)
                if self._closed:
                    return
                self._busy = True
                dev = self.dev
                if not job:
                    dps, self._pending = self._pending, {}
                    force, self._force = self._force, set()
                    leaving = self._saved is not None and not self._music
                    self._last_send = time.time()
                    # so o que mudou: repetir "ligar" ou "modo cor" faz a lampada refazer a
                    # transicao (fade) e a troca de cor parece atrasada. A restauracao vai sempre:
                    # os quadros mudaram a saida da lampada sem mudar o que ela informa
                    dps = {k: v for k, v in dps.items() if k in force or self._known.get(k) != v}
            if job:
                self._send_music(dev, job)
                continue
            if not dev:
                continue
            r = None
            if dps:
                with self._io:
                    try:
                        r = dev.set_multiple_values(dps)
                    except Exception as e:  # noqa: BLE001
                        r = {"Error": str(e)}
            if isinstance(r, dict) and "Error" in r:
                self._lost(dev, r["Error"])
                continue
            with self._cv:
                self._known.update(dps)
                if leaving and not self._music:  # a lampada saiu da musica
                    self._saved = None

    def power(self, on):
        return self._command({"on": bool(on)}, lambda sc, s: {sc.switch: s["on"]})

    def brightness(self, pct):
        pct = max(1, min(100, int(pct)))

        def changes(s):
            # cena/musica nao tem brilho proprio: volta para o branco
            mode = "colour" if s["mode"] == "colour" and self.scheme.colour else "white"
            return {"on": True, "bright": pct, "mode": mode}

        def dps(sc, s):
            if s["mode"] == "colour":
                return {sc.switch: True, sc.colour: sc.colour_raw(s["h"], s["s"], pct)}
            d = {sc.switch: True, sc.bright: sc.bright_raw(pct) if sc.bright else None}
            if sc.mode:
                d[sc.mode] = "white"
            return d
        return self._command(changes, dps)

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

    # ----- musica -----
    @property
    def music_active(self):
        return self._music is not None

    def music_capable(self):
        return self.dev is not None and self.scheme.colour is not None

    def music_start(self, dp="27"):
        with self._cv:
            sc = self.scheme
            if self._closed or not self.dev or sc.colour is None:
                return False
            # tipo B com DP de modo: quadros instantaneos no 27/28; senao (tipo A) so o DP de cor, devagar
            live = sc.kind == "B" and sc.mode is not None
            dp = str(dp) if str(dp) in ("27", "28") else "27"
            if self._music:
                if live:
                    self._music["dp"] = dp
                return True
            if self._saved is None:  # senao a saida anterior ainda nao chegou e o _known diz "music"
                self._saved = self._restore_point()
            mode = "music" if live else "colour"
            enter = {sc.switch: True, sc.mode: mode} if sc.mode else {sc.switch: True}
            self._music = {"dp": dp if live else sc.colour, "live": live, "mode": mode, "enter": enter,
                           "gap": MUSIC_GAP_LIVE if live else MUSIC_GAP_COLOUR}
            self._frame = self._last_frame = self._last_raw = self._beat = None
            self._music_fails = 0
            self._enter()
            self.state.update(on=True, mode="music", music=True)
            snapshot = dict(self.state)
        self.on_change(snapshot)
        return True

    def music_frame(self, h, s, v, jump=True, beat=False):
        """Guarda o quadro mais recente; o worker manda quando o intervalo da lampada deixa.
        beat: quadro de golpe, sai antes; um quadro comum nao toma o lugar de um de golpe ainda na fila
        (devolve False: quem chama manda de novo depois)."""
        with self._cv:
            if not self._music or (self._beat is not None and not beat):
                return False
            self._frame = (int(round(h)) % 360, max(0, min(100, int(round(s)))), max(0, min(100, int(round(v)))),
                           bool(jump))
            self._beat = time.monotonic() if beat else None
            self._cv.notify_all()
            return True

    def music_stop(self):
        with self._cv:
            if not self._music:
                return
            restore = self._end_music()
            self._pending.update(restore)
            self._force.update(restore)
            self._cv.notify_all()
            snapshot = dict(self.state)
        self.on_change(snapshot)

    def wait_idle(self, timeout):
        """Espera o que esta na fila chegar na lampada (ao fechar o app). True se nada ficou pendente."""
        end = time.monotonic() + timeout
        with self._cv:
            while (self._pending or self._busy) and not self._closed:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
        return True

    def _enter(self):
        """Com _cv: (re)entra na musica. O primeiro quadro vai logo depois, em outro pacote (no mesmo
        a lampada rejeita o quadro ou o vermelho de entrada passa por cima dele)."""
        m = self._music
        self._pending.update(m["enter"])
        self._fresh = True
        self._sent = max(self._sent, time.monotonic() - m["gap"])
        self._cv.notify_all()

    def _restore_point(self):
        """Com _cv: DPs que voltam a lampada ao que ela era antes da musica, e o estado do painel."""
        sc, s, k = self.scheme, self.state, self._known
        mode = k.get(sc.mode) if sc.mode else None
        if not isinstance(mode, str) or mode == "music":
            mode = "colour" if s["mode"] == "colour" else "white"
        dps = {sc.mode: mode} if sc.mode else {}
        if mode.startswith("colour"):
            dps[sc.colour] = k.get(sc.colour) or sc.colour_raw(s["h"], s["s"], s["bright"])
        elif mode.startswith("white"):
            if sc.bright:
                dps[sc.bright] = k.get(sc.bright, sc.bright_raw(s["bright"]))
            if sc.temp:
                dps[sc.temp] = k.get(sc.temp, sc.temp_raw(s["temp"]))
        dps[sc.switch] = bool(k.get(sc.switch, s["on"]))
        state = {f: s[f] for f in ("h", "s", "bright", "temp")}
        state["on"] = dps[sc.switch]
        state["mode"] = "colour" if mode.startswith("colour") else "white" if mode.startswith("white") else mode
        return {"dps": dps, "state": state}

    def _end_music(self, restore=True):
        """Com _cv: sai da musica. Devolve os DPs de restauracao (quem chama poe na fila)."""
        saved = self._saved
        self._music = self._frame = self._beat = None
        self.state["music"] = False
        if not (restore and saved):
            self._saved = None
            return {}
        self.state.update(saved["state"])
        return dict(saved["dps"])

    def _music_job(self):
        """Com _cv: ((quick, dp, raw, drain), None) se a musica tem algo para mandar agora; senao
        (None, segundos ate ter), None = ate alguem avisar."""
        m = self._music
        if not m or not self.dev:
            return None, None
        now = time.monotonic()
        frame, beat = self._frame, self._beat
        if beat is not None and m["live"]:
            wait = max(self._sent + MUSIC_GAP_BEAT, self._sent2 + MUSIC_GAP_PAIR) - now
        else:
            wait = self._sent + m["gap"] - now
        if frame is not None and wait > 0:
            return None, wait
        job = None
        if frame is not None:
            self._frame, self._last_frame, self._beat = None, frame, None
            if m["live"]:
                if self._fresh:  # a lampada ignora quadro igual ao anterior: o primeiro de cada entrada troca o T
                    self._t ^= 1
                self._last_raw = music_raw(*frame, temp=self._t)
                job = (True, m["dp"], self._last_raw, self._fresh)
                self._fresh = False
            else:
                raw = self.scheme.colour_raw(*frame[:3])
                if raw != self._known.get(m["dp"]):  # igual: a lampada ja esta nessa cor
                    job = (False, m["dp"], raw, False)
        if job is None:
            wait = self._sent + MUSIC_KEEPALIVE - now
            if wait > 0:
                return None, wait
            # parada ha um tempo: repete o ultimo quadro (a lampada ignora) ou heartbeat (tipo A: repetir
            # a cor faria fade e gravacao)
            job = (True, m["dp"], self._last_raw if m["live"] else None, False)
        self._sent2, self._sent = self._sent, now
        if MUSIC_DEBUG and beat is not None and frame is not None:
            job = job + (beat,)
        return job, None

    def _send_music(self, dev, job):
        quick, dp, raw, drain = job[:4]
        t_send = time.monotonic()
        with self._io:
            try:
                if quick:
                    replies = self._quick(dev, dp, raw, drain)
                else:  # tipo A: caminho normal; a lampada responde com o relatorio do DP de cor
                    replies = [dev.set_value(dp, raw)]
            except Exception as e:  # noqa: BLE001
                replies = [{"Error": str(e)}]
        for r in replies:
            if isinstance(r, dict) and isinstance(r.get("dps"), dict):
                self._report(dev, r["dps"])
        r = replies[-1]
        if len(job) > 4:  # LUME_MUSIC_DEBUG: quadro de golpe
            t_ack = time.monotonic()
            print(f"lume-music lampada {self.id}: golpe na fila {job[4]:.3f}, enviado +{1000 * (t_send - job[4]):.0f} ms,"
                  f" ACK +{1000 * (t_ack - job[4]):.0f} ms{' ERRO' if isinstance(r, dict) and 'Error' in r else ''}",
                  flush=True)
        if isinstance(r, dict) and "Error" in r:
            with self._cv:
                if dev is self.dev:
                    self._music_fails += 1
                give_up = self._music_fails >= MUSIC_FAILS
            if give_up:  # a lampada recusa os quadros mas responde ao status: sem isso reconecta sem parar
                self.music_stop()
            self._lost(dev, r["Error"])
        else:
            with self._cv:
                if dev is self.dev:
                    self._music_fails = 0
                    if not quick:
                        self._known[dp] = raw

    @staticmethod
    def _quick(dev, dp, raw, drain):
        """Quadro (ou heartbeat, raw None) esperando so o ACK. Os DPs 27/28 nunca geram relatorio: com o
        retry padrao da tinytuya cada quadro ficaria preso ate o timeout do socket."""
        limit = getattr(dev, "set_sendRetryLimit", None)  # tinytuya 1.21+: 0 = nunca reenvia o pacote
        dev.set_retry(False)
        dev.set_sendWait(None)
        if limit:
            limit(0)
        replies = []
        try:
            if drain:  # sobras da entrada na musica (relatorios) atrasariam a leitura de cada ACK
                dev.set_socketTimeout(0.1)
                for _n in range(4):
                    r = dev.receive()
                    if not getattr(dev, "raw_recv", None):
                        break
                    replies.append(r)
            dev.set_socketTimeout(1)
            replies.append(dev.heartbeat(nowait=False) if raw is None else dev.set_value(dp, raw))
        finally:  # de volta ao caminho normal: padroes da tinytuya e o timeout do _open
            dev.set_retry(True)
            dev.set_sendWait(0.01)
            if limit:
                limit(None)
            dev.set_socketTimeout(TIMEOUT)
        return replies

    def _report(self, dev, dps):
        """Relatorio que chegou no lugar do ACK de um quadro. Se outro app tirou a lampada da musica
        (mudou o modo ou desligou), a musica acaba aqui sem restaurar nada."""
        with self._cv:
            if dev is not self.dev or self._closed:
                return
            self._known.update(dps)
            m, sc = self._music, self.scheme
            if not m or not (sc.mode in dps and dps[sc.mode] != m["mode"] or dps.get(sc.switch) is False):
                return
            self._end_music(restore=False)
            self._parse(dps)
            snapshot = dict(self.state)
        self.on_change(snapshot)
