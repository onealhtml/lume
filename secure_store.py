"""Guarda segredos (o login do Smart Life) cifrados pela conta do usuario no sistema.
Windows: DPAPI, num arquivo na pasta de configuracao (o Gerenciador de Credenciais falha fora da
sessao interativa e limita o tamanho). macOS: Chaves. Linux: Secret Service (GNOME Keyring, KWallet).
Sem nenhum desses, nada e guardado e o app pede o QR de novo."""
import ctypes
import os
import sys

import core

SERVICE = "Lume"


# ----- Windows: DPAPI -----
class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError(ctypes.GetLastError(), "DPAPI")
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        kernel32.LocalFree(dst.data)


def _win_path(name):
    return os.path.join(core.config_dir(), f"{name}.secret")


# ----- API -----
def save(name, text):
    """True se ficou guardado com seguranca."""
    try:
        if sys.platform == "win32":
            os.makedirs(core.config_dir(), exist_ok=True)
            tmp = _win_path(name) + ".tmp"  # grava inteiro e troca: quem le nunca pega o arquivo pela metade
            with open(tmp, "wb") as f:
                f.write(_dpapi(text.encode("utf-8"), True))
            os.replace(tmp, _win_path(name))
        else:
            import keyring
            keyring.set_password(SERVICE, name, text)
        return True
    except Exception:  # noqa: BLE001 - sem cofre no sistema: simplesmente nao guarda
        return False


def load(name):
    try:
        if sys.platform == "win32":
            with open(_win_path(name), "rb") as f:
                return _dpapi(f.read(), False).decode("utf-8")
        import keyring
        return keyring.get_password(SERVICE, name)
    except Exception:  # noqa: BLE001
        return None


def delete(name):
    try:
        if sys.platform == "win32":
            os.remove(_win_path(name))
        else:
            import keyring
            keyring.delete_password(SERVICE, name)
    except Exception:  # noqa: BLE001
        pass
