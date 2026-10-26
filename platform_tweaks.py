"""Ajustes especificos de cada sistema. Tudo aqui e opcional: se falhar, o app funciona sem o enfeite."""
import ctypes
import ctypes.util
import sys

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def before_app():
    """Antes de criar a QApplication."""
    if IS_WIN:
        # identidade propria no Windows, em vez de aparecer como "Python"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Lume")


def after_app():
    """Depois de criar a QApplication."""
    if IS_MAC:
        _mac_hide_dock_icon()


def _mac_hide_dock_icon():
    # NSApp.setActivationPolicy(.accessory): vive so na barra de menus, sem icone no Dock
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_msgSend.restype = ctypes.c_void_p
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        app = objc.objc_msgSend(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        objc.objc_msgSend(app, objc.sel_registerName(b"setActivationPolicy:"), 1)
    except (OSError, AttributeError):
        pass


class _AccentPolicy(ctypes.Structure):
    _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int), ("color", ctypes.c_uint), ("anim", ctypes.c_int)]


class _CompositionData(ctypes.Structure):
    _fields_ = [("attrib", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]


def _dwm_attr(win_id, attr, value):
    v = ctypes.c_int(value)
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(win_id, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0


def style_window(win_id, mode="blur"):
    """Vidro do sistema atras da janela. Retorna True se o sistema desenha fundo e cantos
    (a interface entao nao arredonda nem pinta o fundo opaco).
    mode: "blur" = desfoque leve, fundo bem visivel;
          "acrylic" = acrilico com tinta propria quase transparente;
          "system" = acrilico de sistema do Windows 11 (desfoque forte e camada cinza)."""
    if not IS_WIN or sys.getwindowsversion().build < 22000:
        return False
    try:
        _dwm_attr(win_id, 20, 1)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        _dwm_attr(win_id, 33, 2)  # DWMWA_WINDOW_CORNER_PREFERENCE = arredondado
        if mode == "system":
            margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(win_id, margins)
            return _dwm_attr(win_id, 38, 3)  # DWMWA_SYSTEMBACKDROP_TYPE = acrilico (com camada cinza fixa)
        if mode == "acrylic":
            # acrilico com tinta escolhida (como o TranslucentTB): cor ABGR, aqui quase transparente
            accent = _AccentPolicy(4, 2, 0x18141210, 0)
        else:
            # desfoque classico (ACCENT_ENABLE_BLURBEHIND): mais leve, sem ruido nem tinta
            accent = _AccentPolicy(3, 0, 0, 0)
        data = _CompositionData(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(win_id, ctypes.byref(data)))
    except (OSError, AttributeError):
        return False


def focus_window(win_id):
    """No Windows, SetForegroundWindow e bloqueado quando outro processo tem o foco (o clique
    na bandeja devolve o foco a barra de tarefas); ligar a fila de entrada libera a troca."""
    if not IS_WIN:
        return
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = kernel32.GetCurrentThreadId()
    attached = fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
    user32.BringWindowToTop(win_id)
    user32.SetForegroundWindow(win_id)
    if attached:
        user32.AttachThreadInput(me, fg_thread, False)
