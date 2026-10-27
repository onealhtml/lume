"""Login no Smart Life por QR code (mesmo fluxo do Home Assistant) para pegar id e local_key das lampadas.
O login fica guardado cifrado (secure_store) para listar as lampadas de novo sem escanear o QR."""
import base64
import io
import json
import threading

import qrcode
from tuya_sharing import LoginControl, Manager, SharingTokenListener
from tuya_sharing.exceptions import ApiRequestException

import secure_store
from i18n import _

# credenciais publicas da integracao Tuya do Home Assistant
CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA = "haauthorize"
LIGHT_CATEGORIES = ("dj", "dd", "dc", "xdd", "fwd")
SESSION = "smartlife"  # nome do segredo guardado

_login = LoginControl()
_lock = threading.Lock()
_generation = 0  # muda a cada login/saida: token renovado de um login antigo nao e gravado


class SessionExpired(Exception):
    """O login guardado nao vale mais: e preciso escanear o QR de novo."""


def start(user_code):
    """Retorna {"token", "qr"} (qr = PNG em data URI) ou {"error"}."""
    resp = _login.qr_code(CLIENT_ID, SCHEMA, user_code)
    if not resp.get("success"):
        return {"error": resp.get("msg") or _("Invalid user code")}
    token = resp["result"]["qrcode"]
    buf = io.BytesIO()
    qrcode.make(f"tuyaSmart--qrLogin?token={token}", border=1).save(buf, format="PNG")
    return {"token": token, "qr": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}


def poll(token, user_code):
    """None enquanto o QR nao foi confirmado; depois {"devices", "remembered"} e o login fica guardado."""
    ok, info = _login.login_result(token, CLIENT_ID, user_code)
    if not ok:
        return None
    global _generation
    session = {"user_code": user_code, "terminal_id": info["terminal_id"], "endpoint": info["endpoint"],
               "token": {k: info[k] for k in ("t", "uid", "expire_time", "access_token", "refresh_token")}}
    with _lock:
        _generation += 1
        remembered = secure_store.save(SESSION, json.dumps(session))
    return {"devices": _devices(session, remembered), "remembered": remembered}


def signed_in():
    return secure_store.load(SESSION) is not None


def devices():
    """Lampadas da conta usando o login guardado. SessionExpired se ele nao vale mais."""
    raw = secure_store.load(SESSION)
    if not raw:
        raise SessionExpired()
    return _devices(json.loads(raw), True)


def sign_out():
    global _generation
    with _lock:
        _generation += 1
        raw = secure_store.load(SESSION)
        secure_store.delete(SESSION)
    if raw:
        try:  # invalida o token no servidor tambem
            s = json.loads(raw)
            Manager(CLIENT_ID, s["user_code"], s["terminal_id"], s["endpoint"], s["token"]).unload()
        except Exception:  # noqa: BLE001 - sem rede: o token so expira sozinho
            pass


class _SaveRefreshed(SharingTokenListener):
    """A biblioteca renova o token de acesso sozinha; o novo tem que ser guardado no lugar do velho."""

    def __init__(self, session, remember):
        self.session, self.remember, self.generation = session, remember, _generation

    def update_token(self, token_info):
        self.session["token"] = token_info
        with _lock:
            if self.remember and self.generation == _generation:  # nao ressuscita depois de "Sair"
                secure_store.save(SESSION, json.dumps(self.session))


def _devices(session, remember):
    manager = Manager(CLIENT_ID, session["user_code"], session["terminal_id"], session["endpoint"],
                      session["token"], _SaveRefreshed(session, remember))
    # update_device_cache engole erros e devolve lista vazia; aqui o servidor recusar vira "faca login
    # de novo" (o login guardado nao vale mais) e falta de rede vira erro, sem apagar o login
    try:
        check = manager.customer_api.get("/v1.0/m/life/users/homes")
    except ApiRequestException as e:
        if remember:
            with _lock:
                secure_store.delete(SESSION)
        raise SessionExpired(str(e)) from e
    if check is None:
        raise ConnectionError(_("no response"))
    manager.update_device_cache()
    found = [{"id": d.id, "key": d.local_key, "name": d.name, "category": d.category,
              "light": d.category in LIGHT_CATEGORIES} for d in manager.device_map.values()]
    return sorted(found, key=lambda d: (not d["light"], d["name"].lower()))
