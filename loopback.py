"""Captura do audio que o PC esta tocando (loopback da saida padrao): WASAPI no Windows, Pulse/PipeWire no Linux.
O soundcard so e importado dentro da thread de captura, e nada aqui levanta excecao para o app."""
import atexit
import os
import sys
import threading
import time
import warnings

SAMPLERATE = 48000
BLOCK = 1024           # 21 ms a 48 kHz
POLL_DEFAULT_S = 2.0   # de quanto em quanto tempo confere se a saida padrao mudou
STALL_S = 0.25         # record() parado mais que isso: o servidor acumulou audio velho, reabrir zera
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def _why(e):
    # texto curto para a interface (o AssertionError do soundcard vem sem mensagem)
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def _prepare_import():
    # o soundcard acha o ole32 pelo ctypes.util.find_library, que no Windows so procura no PATH
    if IS_WINDOWS:
        sysdir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        if sysdir.lower() not in os.environ.get("PATH", "").lower().split(os.pathsep):
            os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + sysdir


def _com_enter():
    """CoInitializeEx(MTA) nesta thread, sempre DEPOIS do import (com o COM ja iniciado o import falha).
    Devolve o ole32 quando o fim da thread precisa do CoUninitialize."""
    if not IS_WINDOWS:
        return None
    import ctypes
    ole32 = ctypes.windll.ole32
    hr = ole32.CoInitializeEx(None, 0) & 0xFFFFFFFF  # 0 = COINIT_MULTITHREADED
    if hr in (0, 1):        # S_OK / S_FALSE: tem que ser balanceado
        return ole32
    if hr == 0x80010106:    # RPC_E_CHANGED_MODE: a thread ja e STA, segue sem balancear
        return None
    raise OSError("CoInitializeEx failed: 0x%08X" % hr)


def _pulse_ready(sc):
    if not IS_LINUX:
        return True
    scp = sc.pulseaudio
    return scp._pulse._pa_context_get_state(scp._pulse.context) == scp._pa.PA_CONTEXT_READY


def _pulse_alive(sc):
    """O soundcard tem um contexto Pulse global: se o servidor reinicia, a proxima chamada de descoberta
    faz o libpulse abortar o processo inteiro. Confere e recria antes. API interna: soundcard==0.4.6 fixo."""
    if _pulse_ready(sc):
        return True
    scp = sc.pulseaudio
    # em duas etapas: o __init__ liga a thread do mainloop antes do assert, e com o servidor fora ela
    # ficaria rodando (uma por tentativa, a cada 5 s)
    pa = scp._PulseAudio.__new__(scp._PulseAudio)
    try:
        pa.__init__()
        # com python -O o assert do soundcard nao roda: confere aqui que o contexto ficou pronto
        ok = pa._pa_context_get_state(pa.context) == scp._pa.PA_CONTEXT_READY
    except Exception:  # noqa: BLE001  servidor ainda fora (AssertionError)
        ok = False
    if not ok:
        ml = getattr(pa, "mainloop", None)
        if ml is not None:
            scp._pa.pa_threaded_mainloop_stop(ml)
            ctx = getattr(pa, "context", None)
            if ctx is not None:
                scp._pa.pa_context_disconnect(ctx)
                scp._pa.pa_context_unref(ctx)
            scp._pa.pa_threaded_mainloop_free(ml)
        return False
    scp._pulse = pa  # as funcoes do modulo leem o global a cada chamada
    atexit.register(pa._shutdown)
    try:
        sc.set_name("Lume")  # o contexto novo nasce com o nome do script
    except Exception:  # noqa: BLE001
        pass
    return True


def _name(dev, fallback):
    # nome para a interface; no Windows ler propriedades de alguns dispositivos falha
    try:
        return str(dev.name) or fallback
    except Exception:  # noqa: BLE001
        return fallback


class LoopbackCapture:
    """Thread que grava o loopback e entrega blocos mono float32 de BLOCK amostras (~47 por segundo).
    on_block roda nessa thread e tem que ser rapido. on_state(state, detail): "running" (nome da saida),
    "reopen" (motivo), "no-device" / "error" / "unavailable" (texto do erro) e "stopped".
    source: id de uma fonte fixa (so para testes); None segue a saida padrao do sistema."""

    def __init__(self, on_block, on_state=None, samplerate=SAMPLERATE, block=BLOCK, source=None):
        self._on_block = on_block
        self._on_state = on_state
        self._sr = samplerate
        self._block = block
        self._source = source
        self._halt = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._halt.clear()
        self._thread = threading.Thread(target=self._run, name="lume-loopback", daemon=True)
        self._thread.start()

    def stop(self, timeout=2.0):
        self._halt.set()
        t, self._thread = self._thread, None
        if t and t is not threading.current_thread():
            t.join(timeout)  # record() volta em ~1 bloco; daemon cobre o pior caso

    def _state(self, state, detail):
        if self._on_state:
            try:
                self._on_state(state, detail)
            except Exception:  # noqa: BLE001  erro de quem ouve nao para a captura
                pass

    def _open(self, sc):
        """(id para comparar depois, nome para a interface, microfone de loopback)."""
        if self._source:
            mic = sc.get_microphone(self._source, include_loopback=True)
            return self._source, _name(mic, self._source), mic
        spk = sc.default_speaker()
        # Windows: loopback montado direto da saida padrao; get_microphone le o nome de TODOS os dispositivos
        # e um com propriedade quebrada derruba a busca (API interna, soundcard==0.4.6 fixo)
        # Pulse/PipeWire: a fonte "<saida>.monitor"
        if IS_WINDOWS:
            mic = sc.mediafoundation._Microphone(spk, isloopback=True)
        else:
            mic = sc.get_microphone(spk.id + ".monitor", include_loopback=True)
        return spk.id, _name(spk, spk.id), mic

    def _current_id(self, sc):
        return self._source or sc.default_speaker().id

    def _run(self):
        _prepare_import()
        try:
            # aqui e nao no topo: no Windows o import inicia o COM nesta thread (na do Qt quebraria o
            # OleInitialize); no Linux ele conecta no servidor Pulse
            import soundcard as sc
        except Exception as e:  # noqa: BLE001  OSError sem libpulse, AssertionError sem servidor, RuntimeError do COM
            detail = "no PulseAudio/PipeWire server" if IS_LINUX and isinstance(e, AssertionError) else _why(e)
            self._state("unavailable", detail)
            return
        try:
            com = _com_enter()
        except OSError as e:
            self._state("unavailable", _why(e))
            return
        if IS_LINUX:
            try:
                # so com o contexto vivo: se o servidor reiniciou com a musica desligada, o set_name no
                # contexto morto faz o libpulse abortar o processo inteiro (nao vira excecao)
                if _pulse_alive(sc):
                    sc.set_name("Lume")  # nome que aparece no controle de volume do sistema
            except Exception:  # noqa: BLE001
                pass
        warnings.filterwarnings("ignore", message="data discontinuity")  # aviso do soundcard no Windows
        backoff = 0.5
        try:
            while not self._halt.is_set():
                try:
                    if not _pulse_alive(sc):
                        raise RuntimeError("PulseAudio/PipeWire server unavailable")
                    dev_id, name, mic = self._open(sc)
                except Exception as e:  # noqa: BLE001  sem saida de audio, servidor fora, dispositivo sumindo
                    self._state("no-device", _why(e))
                    self._halt.wait(backoff)
                    backoff = min(backoff * 2, 5.0)
                    continue
                t0 = time.monotonic()
                try:
                    reason = self._pump(sc, dev_id, name, mic)
                except Exception as e:  # noqa: BLE001  Windows 0x88890004 / 0x8889000A; Linux FAILED, TypeError
                    if time.monotonic() - t0 > 5.0:  # rodou um tempo: falha nova, tenta logo
                        backoff = 0.5
                    self._state("error", _why(e))
                    self._halt.wait(backoff)  # falha que se repete (dispositivo exclusivo) nao gira 2x por segundo
                    backoff = min(backoff * 2, 5.0)
                    continue
                backoff = 0.5
                if not self._halt.is_set():
                    self._state("reopen", reason)
        finally:
            if com is not None:
                com.CoUninitialize()
            self._state("stopped", None)

    def _pump(self, sc, dev_id, name, mic):
        sr, n = self._sr, self._block
        # Linux: blocksize vira o fragsize do Pulse, ou seja a latencia (sem ele ~1.9 s de atraso)
        # Windows: blocksize e a capacidade do buffer WASAPI; folga de 100 ms evita "data discontinuity"
        rec = mic.recorder(samplerate=sr, channels=2, blocksize=sr // 10 if IS_WINDOWS else n)
        try:
            rec.__enter__()
        except Exception:
            # Linux: stream FAILED fica alocado; com stream NULL o pa_stream_disconnect aborta o processo
            if IS_LINUX and getattr(rec, "stream", None):  # ponteiro NULL do cffi e falso
                try:
                    rec.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
            raise
        try:
            self._state("running", name)
            next_poll, last = time.monotonic() + POLL_DEFAULT_S, None  # 1o bloco pode demorar (saida acordando)
            while not self._halt.is_set():
                data = rec.record(numframes=n)  # (n, 2) float32, bloqueia ~n/sr
                now = time.monotonic()
                if last is not None and now - last > STALL_S:
                    return "stalled %.0f ms" % (1000 * (now - last))
                last = now
                self._on_block(data.mean(axis=1))
                if now >= next_poll:
                    next_poll = now + POLL_DEFAULT_S
                    if not _pulse_ready(sc):  # recria so depois de fechar este stream
                        return "pulse context lost"
                    if self._current_id(sc) != dev_id:
                        return "default output changed"
            return "stop"
        finally:
            try:
                rec.__exit__(None, None, None)
            except Exception:  # noqa: BLE001  dispositivo ja invalidado
                pass
