"""Pega id/local_key dos aparelhos via login por QR code no app Smart Life (mesmo fluxo do Home Assistant)."""
import base64
import io

import qrcode
from tuya_sharing import LoginControl, Manager

from i18n import _

# credenciais publicas da integracao Tuya do Home Assistant
CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA = "haauthorize"
LIGHT_CATEGORIES = ("dj", "dd", "dc", "xdd", "fwd")

_login = LoginControl()


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
    """None enquanto o QR nao foi confirmado; depois a lista de aparelhos (luzes primeiro)."""
    ok, info = _login.login_result(token, CLIENT_ID, user_code)
    if not ok:
        return None
    manager = Manager(CLIENT_ID, user_code, info["terminal_id"], info["endpoint"], info)
    manager.update_device_cache()
    devices = [{"id": d.id, "key": d.local_key, "name": d.name, "category": d.category}
               for d in manager.device_map.values()]
    return sorted(devices, key=lambda d: d["category"] not in LIGHT_CATEGORIES)
