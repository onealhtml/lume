"""Modo musica: analisa o audio que o PC esta tocando e transforma em cor e brilho para as lampadas.
So numpy; o audio vem do loopback.py. Sem Qt: MusicSync avisa por callbacks chamados das suas threads."""
import math
import random
import sys
import threading
import time

import numpy as np

import loopback

SUPPORTED = sys.platform == "win32" or sys.platform.startswith("linux")  # macOS nao tem loopback nativo
STYLES = ("pulse", "flow", "party")  # o mesmo que core.MUSIC_STYLES

# ----- analise (quadro: mono, Hann, rfft de 2*hop amostras a cada hop de ~21 ms, tudo em dB) -----
F_LO, F_HI, N_BANDS = 35.0, 14000.0, 16      # bandas logaritmicas (o WLED tambem usa 16)
BASS, MID, TREB = (0, 4), (4, 11), (11, 16)  # 35-157 Hz, 157-2155 Hz, 2.2-14 kHz
EPS = 1e-12
SILENCE_DB = -120.0

GATE_HYST_DB = 6.0       # o portao fecha 6 dB abaixo de onde abre...
GATE_HOLD_S = 1.0        # ...depois de 1 s assim (pausas da fala nao derrubam a lampada)
GATE_RESET_S = 20.0      # silencio maior que isso: o AGC recomeca no proximo som
ACT_UP_S, ACT_DOWN_S = 0.15, 1.2   # "active" sobe rapido e desce devagar (a lampada volta calma ao repouso)

PEAK_DECAY_DBPS = 20.0   # AGC 1: o pico cai 20 dB/s
AGC_ATTACK_S = 0.4       # AGC 2: a referencia segue o pico subindo...
AGC_RELEASE_S = 3.0      # ...e descendo (WLED agcFollowSlow 2-15 s)
AGC_FAST_RELEASE_S = 0.8 # ...mais rapido com o pico 15 dB abaixo (volume baixou)
AGC_FAST_ZONE_DB = 15.0
AGC_MAX_BOOST_DB = (15.0, 10.0, 20.0)  # grave/medio/agudo nunca mais que isso abaixo da referencia geral:
                                       # podcast sem grave nao vira linha de baixo amplificada
ENV_RELEASE_S = (0.25, 0.15, 0.20, 0.30)   # geral, grave, medio, agudo; o ataque e instantaneo
BAL_AVG_S, BAL_SMOOTH_S, BAL_SCALE_DB = 6.0, 0.3, 12.0
PUNCH_BASE_S, PUNCH_RELEASE_S = 0.5, 0.15  # bumbo = grave acima da propria media de 0.5 s
BAR_WINDOW_DB = 24.0     # visualizador: dB abaixo do pico recente de cada banda que viram 0
BAR_DECAY_DBPS = 4.0     # o pico de cada barra cai devagar
BAR_MAX_BOOST_DB = 18.0  # pico da barra nunca abaixo de ref-18 dB: banda quase vazia fica baixa, nao cheia
BAR_RELEASE_S = 0.12

FLUX_FLOOR_DB = 50.0     # bandas cortadas em ref-50 dB: chiado nao gera fluxo
MID_SUPPRESS = 0.5       # batida = fluxo do grave - 0.5 * fluxo do medio: silabas e acordes contam menos
STATS_S = 1.5            # janela das estatisticas do limiar adaptativo
MIN_IBI_S = 0.30         # no maximo 200 BPM de batidas (os 80-100 ms do WLED sao para fita de LED)
MIN_IOI_S = 0.15         # onsets (o party usa quando nao ha batida)
ONSET_DELTA = 0.6        # delta dos onsets = 0.6 * o das batidas (a media de 16 bandas e menor)

TEMPO_WIN_S, TEMPO_EVERY_S = 6.0, 0.5
TEMPO_MIN_S, TEMPO_MAX_S = 0.30, 1.0       # 60-200 BPM
TEMPO_PRIOR_S, TEMPO_PRIOR_OCT = 0.5, 1.0  # preferencia em torno de 120 BPM (Ellis 2007)
CLARITY_SMOOTH_S = 1.5
LOCK_CLARITY = 0.35
CLAR_LO, CLAR_SPAN = 0.25, 0.3   # "ritmico" no mapeador: 0 com clareza <= 0.25 (fala), 1 com >= 0.55
BEAT_ANCHOR = 0.75       # x hop: a deteccao sai ~16 ms depois do inicio real; a grade ancora antes
PHASE_WINDOW = 0.25      # batida a +-25 % do periodo previsto reancora a grade
MAX_MISSES = 4

# ----- luz -----
FLOOR_V = 10             # %: perto de 0 as lampadas parecem apagadas (tipo B: minimo 10/1000)
GAMMA = 1.6              # contraste percebido: v = piso + faixa * y**1.6
FLASH_DECAY_S = 0.20     # o flash da batida ainda passa de 50 % dois ticks depois (15 Hz)
GOLDEN = 137.508         # angulo de ouro: cores seguidas do party bem diferentes

# ----- MusicSync -----
TICK_S = 1 / 15
SILENT_S = 2.0           # tanto tempo sem som: status "silent"
SOUND_MS = 1e-8          # media do quadrado acima disso (-80 dBFS) e som tocando
STALE_S = 0.5            # captura sem blocos por mais que isso (erro, sem dispositivo): lampada em repouso


def _lerp3(s, a0, a50, a100):
    s = min(max(float(s), 0.0), 100.0)
    return a0 + (a50 - a0) * s / 50 if s <= 50 else a50 + (a100 - a50) * (s - 50) / 50


class SensParams:
    """O que o botao de sensibilidade (0-100) muda; valores em 0 / 50 / 100."""
    __slots__ = ("agc_window_db", "beat_k", "beat_delta_db", "gate_open_dbfs", "punch_db", "depth", "flash")

    def __init__(self, s):
        self.agc_window_db = _lerp3(s, 32, 24, 16)    # dB abaixo da referencia que viram 0..1
        self.beat_k = _lerp3(s, 2.0, 1.6, 1.2)         # limiar = media + k * desvio + delta
        self.beat_delta_db = _lerp3(s, 6.5, 5.0, 3.5)  # subida media minima do grave para ser batida
        self.gate_open_dbfs = _lerp3(s, -54, -62, -68)
        self.punch_db = _lerp3(s, 14.0, 10.0, 6.0)     # grave acima da media que vale punch = 1
        self.depth = _lerp3(s, 0.55, 0.8, 1.0)         # quanto da faixa de brilho a musica usa
        self.flash = _lerp3(s, 0.6, 0.9, 1.0)          # forca do flash na batida


_FEATURES = {"wall": 0.0, "active": 0.0, "gate": False, "loud": 0.0, "bass": 0.0, "mid": 0.0, "treble": 0.0,
             "punch": 0.0, "balance": 0.0, "bands": (0.0,) * N_BANDS, "beat": False, "beats": 0,
             "beat_strength": 0.0, "beat_wall": 0.0, "onset": False, "onset_strength": 0.0, "tempo_bpm": 0.0,
             "period": 0.0, "clarity": 0.0, "locked": False, "next_beat": None}


class Features:
    """Saida do analisador. wall: relogio do quadro mais novo; envelopes 0-1; balance -1 grave .. +1 agudo;
    clarity: quanto o som e ritmico (baixo em fala e ambiente); next_beat: proxima batida prevista."""
    __slots__ = tuple(_FEATURES)

    def __init__(self, **kw):
        for k, v in _FEATURES.items():
            setattr(self, k, kw.get(k, v))

    def copy(self, **kw):
        return Features(**{**{k: getattr(self, k) for k in _FEATURES}, **kw})


class MusicAnalyzer:
    """feed() na thread de captura (qualquer tamanho de bloco); take() na thread do tick.
    beat_event acende a cada batida: o tick espera nele e manda o flash na hora."""

    def __init__(self, sample_rate=48000, sensitivity=50, clock=time.monotonic):
        self.sr = sr = int(sample_rate)
        self.hop = 512 if sr <= 26000 else 1024 if sr <= 52000 else 2048   # ~21 ms
        self.nfft = n = 2 * self.hop
        self.dt = dt = self.hop / sr
        self.clock = clock
        self.sens = SensParams(sensitivity)
        self._win = np.hanning(n).astype(np.float32)
        self._norm = 2.0 / (n * float(np.sum(self._win.astype(np.float64) ** 2)))  # -> media quadratica
        edges = np.geomspace(F_LO, min(F_HI, 0.45 * sr), N_BANDS + 1)
        bins = np.round(edges * n / sr).astype(int)
        for i in range(1, len(bins)):  # toda banda com pelo menos um bin
            bins[i] = max(bins[i], bins[i - 1] + 1)
        self._b0, self._b1 = int(bins[0]), int(bins[-1])
        self._idx = bins[:-1] - self._b0
        self._grp_idx = np.array([BASS[0], MID[0], TREB[0]])

        def a(tau):  # coeficiente de suavizacao por quadro
            return 1.0 - math.exp(-dt / tau)
        self._a_gate = a(0.1)
        self._a_act_up, self._a_act_down = a(ACT_UP_S), a(ACT_DOWN_S)
        self._a_att, self._a_rel, self._a_fast = a(AGC_ATTACK_S), a(AGC_RELEASE_S), a(AGC_FAST_RELEASE_S)
        self._a_env = [a(r) for r in ENV_RELEASE_S]
        self._a_balavg, self._a_bal = a(BAL_AVG_S), a(BAL_SMOOTH_S)
        self._a_pbase, self._a_punch = a(PUNCH_BASE_S), a(PUNCH_RELEASE_S)
        self._a_bar = a(BAR_RELEASE_S)
        self._a_stats = a(STATS_S)
        self._peak_step = PEAK_DECAY_DBPS * dt
        self._bar_step = BAR_DECAY_DBPS * dt
        self._tempo_n = int(round(TEMPO_WIN_S / dt))
        self._tempo_every = max(1, int(round(TEMPO_EVERY_S / dt)))
        lags = np.arange(max(2, int(TEMPO_MIN_S / dt)), int(math.ceil(TEMPO_MAX_S / dt)) + 1)
        self._lags = lags
        self._prior = np.exp(-0.5 * (np.log2(lags * dt / TEMPO_PRIOR_S) / TEMPO_PRIOR_OCT) ** 2)

        self._lock = threading.Lock()
        self.beat_event = threading.Event()
        self._frame = np.zeros(n, np.float32)
        self._stage = np.zeros(0, np.float32)
        self._reset_state()

    def set_sensitivity(self, s):
        self.sens = SensParams(s)

    def _reset_state(self):
        self._t = 0.0               # relogio do audio
        self._lg = SILENCE_DB
        self._gate = False
        self._below = 0.0
        self._silent = 1e9
        self._act = 0.0
        self._ref = None            # referencias do AGC (dB): geral, grave, medio, agudo
        self._peak = None
        self._env = [0.0, 0.0, 0.0, 0.0]
        self._balavg = None
        self._bal = 0.0
        self._bbase = None
        self._punch = 0.0
        self._bref = None           # pico recente de cada barra do visualizador
        self._bars = np.zeros(N_BANDS)
        self._lprev = [None, None]
        self._beat_mu = self._beat_var = 0.0
        self._on_mu = self._on_var = 0.0
        self._last_beat = self._last_onset = -1e9
        self._odf_ring = np.zeros(self._tempo_n, np.float32)
        self._ring_pos = 0
        self._ring_fill = 0
        self._frames = 0
        self._clarity = 0.0
        self._period = 0.0
        self._next_beat = None      # no relogio do audio
        self._misses = 0
        self._out = Features()
        self._acc = None            # acumulado desde o ultimo take()

    def feed(self, block):
        """block: (amostras, canais) ou (amostras,), float ou int16."""
        x = np.asarray(block)
        if x.dtype == np.int16:
            x = x.astype(np.float32) / 32768.0
        if x.ndim == 2:
            x = x.mean(axis=1, dtype=np.float32) if x.shape[1] > 1 else x[:, 0]
        x = x.astype(np.float32, copy=False)
        if not np.isfinite(x).all():
            x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
        stage = np.concatenate((self._stage, x)) if self._stage.size else x
        hop, pos = self.hop, 0
        while stage.size - pos >= hop:
            self._frame[:-hop] = self._frame[hop:]
            self._frame[-hop:] = stage[pos:pos + hop]
            pos += hop
            self._process()
        self._stage = stage[pos:].copy()

    def _process(self):
        dt, sp = self.dt, self.sens
        self._t += dt
        t = self._t
        X = np.fft.rfft(self._frame * self._win)
        p = X.real * X.real + X.imag * X.imag
        bp = np.add.reduceat(p[self._b0:self._b1], self._idx) * self._norm  # potencia das 16 bandas
        lb = 10.0 * np.log10(bp + EPS)
        g = np.add.reduceat(bp, self._grp_idx)
        tot = float(g.sum())
        lvl = [10.0 * math.log10(tot + EPS), 10.0 * math.log10(g[0] + EPS),
               10.0 * math.log10(g[1] + EPS), 10.0 * math.log10(g[2] + EPS)]

        # portao de ruido em dBFS absoluto (loopback nao tem ruido de microfone), com histerese e espera
        self._lg += (max(lvl[0], -100.0) - self._lg) * self._a_gate
        if self._gate:
            self._below = self._below + dt if self._lg < sp.gate_open_dbfs - GATE_HYST_DB else 0.0
            if self._below >= GATE_HOLD_S:
                self._gate, self._silent = False, 0.0
        elif self._lg > sp.gate_open_dbfs:
            self._gate, self._below = True, 0.0
            if self._ref is None or self._silent > GATE_RESET_S:
                self._ref = [lvl[0] + 3.0] + [max(lvl[i], lvl[0] - AGC_MAX_BOOST_DB[i - 1]) + 3.0 for i in (1, 2, 3)]
                self._peak = list(self._ref)
                self._balavg = [lvl[1], lvl[3]]
                self._bbase = None
                self._bref = None
        else:
            self._silent += dt
        gate = self._gate
        self._act += ((1.0 if gate else 0.0) - self._act) * (self._a_act_up if gate else self._a_act_down)

        env = self._env
        if gate:
            # AGC: pico (ataque instantaneo, cai 20 dB/s) -> referencia com ataque e liberacao
            ref, peak = self._ref, self._peak
            for i in range(4):
                peak[i] = max(lvl[i], peak[i] - self._peak_step)
                d = peak[i] - ref[i]
                a = self._a_att if d > 0 else (self._a_fast if d < -AGC_FAST_ZONE_DB else self._a_rel)
                ref[i] += d * a
            win = sp.agc_window_db
            for i in range(4):
                r = ref[i] if i == 0 else max(ref[i], ref[0] - AGC_MAX_BOOST_DB[i - 1])
                xi = (lvl[i] - (r - win)) / win
                xi = 0.0 if xi < 0 else 1.0 if xi > 1 else xi
                env[i] = xi if xi > env[i] else env[i] + (xi - env[i]) * self._a_env[i]
            # equilibrio grave x agudo em relacao a media recente
            ba = self._balavg
            ba[0] += (lvl[1] - ba[0]) * self._a_balavg
            ba[1] += (lvl[3] - ba[1]) * self._a_balavg
            b = ((lvl[3] - ba[1]) - (lvl[1] - ba[0])) / BAL_SCALE_DB
            b = -1.0 if b < -1 else 1.0 if b > 1 else b
            self._bal += (b - self._bal) * self._a_bal
            # punch: grave acima da propria media curta (o bumbo salta de uma linha de baixo continua)
            lb_eff = max(lvl[1], ref[0] - AGC_MAX_BOOST_DB[0] - win)  # ignora grave muito abaixo da mistura
            if self._bbase is None:
                self._bbase = lb_eff
            px = (lb_eff - self._bbase) / sp.punch_db
            px = 0.0 if px < 0 else 1.0 if px > 1 else px
            self._bbase += (lb_eff - self._bbase) * self._a_pbase
            self._punch = px if px > self._punch else self._punch + (px - self._punch) * self._a_punch
            # barras: cada banda contra o proprio pico recente, que nunca fica muito abaixo da mistura
            br = lb + 3.0 if self._bref is None else np.maximum(lb, self._bref - self._bar_step)
            self._bref = br = np.maximum(br, ref[0] - BAR_MAX_BOOST_DB)
            x = np.clip((lb - (br - BAR_WINDOW_DB)) / BAR_WINDOW_DB, 0.0, 1.0)
            bars = self._bars
            self._bars = np.where(x > bars, x, bars + (x - bars) * self._a_bar)
        else:
            self._punch *= 1.0 - self._a_punch
            for i in range(4):
                env[i] *= 1.0 - self._a_env[i]
            self._bal *= 1.0 - self._a_bal
            self._bars = self._bars * (1.0 - self._a_bar)

        # fluxo espectral em dB (so subidas, comparando com o quadro n-2: a sobreposicao espalha o ataque)
        floor = (self._ref[0] if self._ref else -60.0) - FLUX_FLOOR_DB
        lc = np.maximum(lb, floor)
        old = self._lprev[0]
        self._lprev = [self._lprev[1], lc]
        beat = onset = False
        bstr = ostr = 0.0
        if old is not None:
            pos = np.maximum(lc - old, 0.0)
            fb = float(pos[BASS[0]:BASS[1]].mean())
            fm = float(pos[MID[0]:MID[1]].mean())
            fa = float(pos.mean())
            odf = max(0.0, fb - MID_SUPPRESS * fm)
            # anel do tempo: o fluxo do bumbo mais um pouco do geral (caixa no 2 e no 4)
            self._odf_ring[self._ring_pos] = odf + 0.5 * fa
            self._ring_pos = (self._ring_pos + 1) % self._tempo_n
            self._ring_fill = min(self._ring_fill + 1, self._tempo_n)
            if gate:
                beat, bstr, self._beat_mu, self._beat_var = self._detect(
                    odf, self._beat_mu, self._beat_var, sp.beat_delta_db, t - self._last_beat >= MIN_IBI_S)
                if beat:
                    self._last_beat = t
                    self.beat_event.set()
                onset, ostr, self._on_mu, self._on_var = self._detect(
                    fa, self._on_mu, self._on_var, ONSET_DELTA * sp.beat_delta_db, t - self._last_onset >= MIN_IOI_S)
                if onset:
                    self._last_onset = t

        # tempo e clareza do pulso (autocorrelacao), a cada 0.5 s
        self._frames += 1
        if self._frames % self._tempo_every == 0:
            self._tempo(gate)
        # grade de batidas: reancora nas detectadas e segue sozinha por algumas perdidas
        locked = self._clarity >= LOCK_CLARITY and self._period > 0
        if locked:
            per = self._period
            if beat:
                if self._next_beat is None or abs(t - self._next_beat) < PHASE_WINDOW * per \
                        or abs(t - (self._next_beat - per)) < PHASE_WINDOW * per or self._misses >= 2:
                    self._next_beat, self._misses = t - BEAT_ANCHOR * dt + per, 0
            elif self._next_beat is not None and t > self._next_beat + PHASE_WINDOW * per:
                self._next_beat += per
                self._misses += 1
                if self._misses > MAX_MISSES:
                    self._next_beat = None
        else:
            self._next_beat, self._misses = None, 0

        wall = self.clock()
        o = Features(wall=wall, active=self._act, gate=gate, loud=env[0], bass=env[1], mid=env[2], treble=env[3],
                     punch=self._punch, balance=self._bal, bands=self._bars, beat=beat, beats=int(beat),
                     beat_strength=bstr, beat_wall=wall if beat else self._out.beat_wall, onset=onset,
                     onset_strength=ostr, tempo_bpm=60.0 / self._period if self._period else 0.0,
                     period=self._period, clarity=self._clarity, locked=locked and self._next_beat is not None,
                     next_beat=(wall + self._next_beat - t) if (locked and self._next_beat is not None) else None)
        with self._lock:
            self._out = o
            a = self._acc
            if a is None:
                self._acc = o.copy()
            else:  # entre dois take(): picos dos envelopes e qualquer batida/onset
                self._acc = o.copy(loud=max(a.loud, o.loud), bass=max(a.bass, o.bass), mid=max(a.mid, o.mid),
                                   treble=max(a.treble, o.treble), punch=max(a.punch, o.punch),
                                   bands=np.maximum(a.bands, o.bands), beat=a.beat or beat, beats=a.beats + int(beat),
                                   beat_strength=max(a.beat_strength, bstr), onset=a.onset or onset,
                                   onset_strength=max(a.onset_strength, ostr))

    def _detect(self, odf, mu, var, delta, allowed):
        """Limiar adaptativo (media + k*desvio + delta de ~1.5 s) com periodo refratario. Quadros acima
        do limiar ou no refratario entram cortados e 4x mais devagar: bumbo denso nao sobe o proprio limiar."""
        thr = mu + self.sens.beat_k * math.sqrt(var) + delta
        hit = allowed and odf > thr
        strength = min(1.0, (odf - mu) / (2.5 * (thr - mu))) if hit else 0.0
        a = self._a_stats
        if odf > thr or not allowed:
            odf, a = min(odf, thr), a * 0.25
        d = odf - mu
        mu += a * d
        var += a * (d * d - var)
        return hit, strength, mu, var

    def _tempo(self, gate):
        a = 1.0 - math.exp(-TEMPO_EVERY_S / CLARITY_SMOOTH_S)
        if not gate or self._ring_fill < self._tempo_n // 2:
            self._clarity *= 1.0 - a
            if self._clarity < 0.05:
                self._period = 0.0
            return
        n = self._ring_fill
        x = np.roll(self._odf_ring, -self._ring_pos)[-n:].astype(np.float64)
        x -= x.mean()
        ac = np.correlate(x, x, "full")[n - 1:]
        if ac[0] <= 1e-9:
            self._clarity *= 1.0 - a
            return
        lags = self._lags[self._lags < n // 2]
        acn = ac[:n // 2 + 1] / ac[0] * (n / (n - np.arange(n // 2 + 1)))  # sem vies, normalizada
        score = acn[lags] * self._prior[:len(lags)]
        dbl = 2 * lags
        ok = dbl < len(acn)
        score[ok] += 0.5 * np.maximum(acn[dbl[ok]], 0) * self._prior[:len(lags)][ok]  # favorece o periodo certo
        i = int(np.argmax(score))
        lag = float(lags[i])
        if 0 < lags[i] < len(acn) - 1:  # interpolacao parabolica
            y0, y1, y2 = acn[lags[i] - 1], acn[lags[i]], acn[lags[i] + 1]
            den = y0 - 2 * y1 + y2
            if den < 0:
                lag += 0.5 * (y0 - y2) / den
        conf = float(min(1.0, max(0.0, acn[lags[i]])))
        per = lag * self.dt
        self._clarity += (conf - self._clarity) * a
        if self._period and abs(per - self._period) < 0.08 * self._period:
            self._period += (per - self._period) * 0.3
        elif conf > 0.3 or not self._period:
            self._period = per

    def take(self):
        """Features com picos e eventos acumulados desde o take() anterior: batida entre ticks nao se perde."""
        with self._lock:
            a, self._acc = self._acc, None
            return a if a is not None else self._out.copy(beat=False, beats=0, beat_strength=0.0,
                                                           onset=False, onset_strength=0.0)


class LightMapper:
    """Features -> (h, s, v) por lampada, a cada tick (10-20 Hz).
    pulse: a cor do usuario, brilho segue o grave com flash na batida; flow: a matiz passeia (mais rapido
    com som alto) e pende com o equilibrio grave x agudo; party: cor nova a cada batida.
    latency: quanto a luz atrasa o som (rede + lampada); com o tempo travado o flash sai antes da batida prevista."""

    def __init__(self, style="pulse", sensitivity=50, base_hue=30, base_sat=100, max_bright=100,
                 n_lamps=1, latency=0.10, rest=0.5, seed=None):
        self.set_style(style)
        self.set_sensitivity(sensitivity)
        self.set_base(base_hue, base_sat)
        self.set_max_bright(max_bright)
        self.n = max(1, int(n_lamps))
        self.latency = float(latency)
        self.rest = rest
        self._rng = random.Random(seed)
        self._now = None
        self._flash = [0.0] * self.n
        self._nbeats = 0
        self._last_fire = -1e9
        self._last_pred = -1e9
        self._last_change = -1e9
        self._last_beatlike = -1e9
        self._phase = 0.0
        self._push = 0.0
        self._bal = 0.0
        self._treb = 0.0
        self._vs = [None] * self.n
        self._party_h = self.base_hue

    def set_style(self, style):
        if style not in STYLES:
            raise ValueError(style)
        self.style = style

    def set_sensitivity(self, s):
        self.sens = SensParams(s)

    def set_base(self, h, s):
        self.base_hue, self.base_sat = float(h) % 360, max(0.0, min(100.0, float(s)))

    def set_max_bright(self, pct):
        self.vmax = max(FLOOR_V + 20.0, min(100.0, float(pct)))

    def resize(self, n):
        """Numero de lampadas mudou: as que ficam mantem o estado."""
        n = max(1, int(n))
        self._flash = (self._flash + [0.0] * n)[:n]
        self._vs = (self._vs + [None] * n)[:n]
        self.n = n

    def _beat_event(self, f, now, tick_dt):
        """(dispara, forca, previsto). Com o tempo travado dispara antes da batida prevista; senao reage
        a detectada. A detectada logo depois de um disparo previsto e a mesma batida: ignora."""
        per = f.period if f.locked else 0.0
        clar = min(1.0, max(0.0, (f.clarity - CLAR_LO) / CLAR_SPAN))
        if self.latency > 0 and f.locked and f.next_beat is not None and clar >= 0.5:
            tp = f.next_beat
            if now >= tp - self.latency - 0.5 * tick_dt and abs(tp - self._last_pred) > 0.5 * per \
                    and now - self._last_fire > 0.5 * per:
                self._last_pred = tp
                return True, max(0.7, f.beat_strength), True
        if f.beat and not (per and now - self._last_fire < 0.6 * per):
            return True, f.beat_strength, False
        return False, 0.0, False

    def tick(self, f, now=None):
        now = f.wall if now is None else now
        dt = 1 / 15 if self._now is None else max(1e-3, min(0.5, now - self._now))
        self._now = now
        sp, a = self.sens, f.active
        clar = min(1.0, max(0.0, (f.clarity - CLAR_LO) / CLAR_SPAN))  # 0 fala/ambiente .. 1 batida firme
        fire, strength, _pred = self._beat_event(f, now, dt) if f.gate else (False, 0.0, False)
        if fire:
            self._last_fire = now
            self._nbeats += 1
        decay = math.exp(-dt / FLASH_DECAY_S)
        amp = sp.flash * (0.35 + 0.65 * clar) * (0.5 + 0.5 * strength) if fire else 0.0
        for i in range(self.n):
            # varias lampadas: a cada batida metade alternada leva o flash inteiro
            ai = amp if (self.n == 1 or (i + self._nbeats) % 2 == 0) else 0.5 * amp
            self._flash[i] = max(self._flash[i] * decay, ai)
        depth = sp.depth
        rng = self.vmax - FLOOR_V
        v_rest = FLOOR_V + rng * self.rest

        if self.style == "pulse":
            drive = 0.6 * f.punch + 0.4 * f.loud
            hs = [(self.base_hue, self.base_sat)] * self.n
            fl = self._flash
        elif self.style == "flow":
            self._phase += dt * (2.0 + 12.0 * f.loud) * a              # graus/s: 2 calmo .. 14 alto
            if fire:
                self._push += 12.0 * strength * (0.3 + 0.7 * clar)      # batidas empurram a matiz
            step = self._push * (1 - math.exp(-dt / 0.3))
            self._push -= step
            self._phase = (self._phase + step) % 360
            self._bal += (f.balance - self._bal) * (1 - math.exp(-dt / 0.6))
            self._treb += (f.treble - self._treb) * (1 - math.exp(-dt / 0.4))
            hue = self.base_hue + self._phase + 60.0 * self._bal
            sat = self.base_sat * (1.0 - 0.2 * self._treb * a)          # chimbal e ar: um pouco mais palido
            spread = 0.0 if self.n == 1 else min(30.0, 120.0 / (self.n - 1))
            hs = [((hue + (i - (self.n - 1) / 2) * spread) % 360, sat) for i in range(self.n)]
            drive = 0.6 * f.loud + 0.4 * f.punch
            fl = [0.6 * x for x in self._flash]
        else:  # party
            min_gap = 1.0 - 0.75 * clar                       # 1 s entre cores na fala, 0.25 s com batida firme
            change = fire and now - self._last_change >= min_gap
            if fire:
                self._last_beatlike = now
            elif f.onset and f.gate and now - self._last_beatlike > 2.5 \
                    and now - self._last_change >= 1.0 + 1.5 * (1 - clar):
                change = True                                  # sem batida faz tempo: segue os onsets fortes
                for i in range(self.n):
                    self._flash[i] = max(self._flash[i], 0.5 * sp.flash)
            if change:
                self._party_h = (self._party_h + GOLDEN + self._rng.uniform(-20, 20)) % 360
                self._last_change = now
            elif f.gate and now - max(self._last_change, self._last_beatlike) > 6.0:
                self._party_h = (self._party_h + 8.0 * dt * a) % 360   # nada ritmico: deriva lenta
            step = 360.0 / self.n if self.n <= 4 else GOLDEN
            hs = [((self._party_h + i * step) % 360, 100.0) for i in range(self.n)]
            drive = 0.15 + 0.5 * f.punch
            fl = self._flash

        # fala/ambiente (clareza baixa): subida limitada e descida mais lenta, silabas nao piscam;
        # batida firme (e todo flash de batida) fica seca
        fall = math.exp(-dt / (0.08 + 0.3 * (1 - clar)))
        rise = 1e9 if fire else dt * (120.0 + 1500.0 * clar)
        out = []
        for i in range(self.n):
            y = 1 - (1 - drive) * (1 - fl[i])  # screen: o flash puxa para o maximo
            y = 0.0 if y < 0 else 1.0 if y > 1 else y
            v = FLOOR_V + rng * ((1 - depth) + depth * y ** GAMMA)
            v = a * v + (1 - a) * v_rest
            vs = self._vs[i]
            vs = v if vs is None else min(v, vs + rise) if v > vs else v + (vs - v) * fall
            self._vs[i] = vs
            h, s = hs[i]
            out.append((int(round(h)) % 360, int(round(s)), max(FLOOR_V, min(100, int(round(vs))))))
        return out


def needs_send(prev, cur, dv=2, dh=3, ds=3):
    """Zona morta: a lampada so recebe mudancas de verdade (o firmware Tuya engasga com enxurrada de ajustes)."""
    if prev is None:
        return True
    d = abs(prev[0] - cur[0]) % 360
    return min(d, 360 - d) >= dh or abs(prev[1] - cur[1]) >= ds or abs(prev[2] - cur[2]) >= dv


class MusicSync:
    """Captura + analise + lampadas. Duas threads: a da captura e a do tick (15 Hz, adiantado nas batidas).
    on_frame(dict) a cada tick e on_status(status, detail) so quando muda, sempre da thread do tick.
    source: fonte fixa para testes; o app nunca passa (segue a saida padrao)."""

    def __init__(self, on_frame, on_status, source=None):
        self._on_frame = on_frame
        self._on_status = on_status
        self._source = source
        self._lock = threading.Lock()  # protege _lamps, _sent e _mapper (tick x interface)
        self._gen = 0                  # cada start/stop invalida avisos atrasados da captura anterior
        self._lamps = []
        self._sent = {}                # ultima cor mandada para cada lampada
        self._dp = "27"
        self._an = None
        self._mapper = None
        self._cap = None
        self._thread = None
        self._halt = None
        self._cap_state = None
        self._cap_detail = ""
        self._last_block = 0.0
        self._last_sound = 0.0

    @property
    def running(self):
        return self._thread is not None

    def start(self, lamps, style, sensitivity, base_hue, base_sat, max_bright, dp="27"):
        if self.running:  # ja ligado: so ajusta
            self.set_style(style)
            self.set_sensitivity(sensitivity)
            self.set_base(base_hue, base_sat)
            self.set_max_bright(max_bright)
            return self.set_lamps(lamps)
        self._dp = dp
        joined = self._join(lamps)
        if not joined:
            return 0
        self._gen += 1
        gen = self._gen
        an = self._an = MusicAnalyzer(loopback.SAMPLERATE, sensitivity)
        with self._lock:
            self._mapper = LightMapper(style if style in STYLES else STYLES[-1], sensitivity, base_hue, base_sat,
                                       max_bright, n_lamps=len(joined))
            self._lamps, self._sent = joined, {}
        self._cap_state, self._cap_detail = None, ""
        self._last_block = 0.0
        self._last_sound = time.monotonic()
        self._halt = threading.Event()
        self._cap = loopback.LoopbackCapture(lambda x: self._block(gen, an, x),
                                             lambda st, d: self._capture_state(gen, st, d), source=self._source)
        self._thread = threading.Thread(target=self._tick, args=(self._halt, an), name="lume-music", daemon=True)
        self._thread.start()
        self._cap.start()
        return len(joined)

    def stop(self):
        th, cap = self._thread, self._cap
        if th is None:
            return
        self._gen += 1
        self._halt.set()
        self._an.beat_event.set()  # acorda o tick
        deadline = time.monotonic() + 2.0
        cap.stop(timeout=1.5)
        if th is not threading.current_thread():
            th.join(max(0.05, deadline - time.monotonic()))
        self._thread = self._cap = None
        with self._lock:
            lamps, self._lamps = self._lamps, []
            self._sent = {}
        for lamp in lamps:
            _call(lamp.music_stop)

    def set_lamps(self, lamps):
        if not self.running:
            return 0
        lamps = list(lamps)
        with self._lock:
            gone = [lamp for lamp in self._lamps if lamp not in lamps]
            self._drop(gone)
            have = list(self._lamps)
        for lamp in gone:
            _call(lamp.music_stop)
        new = self._join([lamp for lamp in lamps if lamp not in have])
        with self._lock:
            if new:
                self._lamps = self._lamps + new
                self._mapper.resize(len(self._lamps))
            return len(self._lamps)

    def set_style(self, style):
        if style in STYLES:
            with self._lock:
                if self._mapper:
                    self._mapper.set_style(style)

    def set_sensitivity(self, value):
        with self._lock:
            if self._mapper:
                self._mapper.set_sensitivity(value)
                self._an.set_sensitivity(value)

    def set_base(self, h, s):
        with self._lock:
            if self._mapper:
                self._mapper.set_base(h, s)

    def set_max_bright(self, pct):
        with self._lock:
            if self._mapper:
                self._mapper.set_max_bright(pct)

    def _join(self, lamps):
        joined = []
        for lamp in lamps:
            if _call(lamp.music_start, self._dp):
                joined.append(lamp)
        return joined

    def _drop(self, gone):
        # chamar com _lock
        if gone:
            self._lamps = [lamp for lamp in self._lamps if lamp not in gone]
            for lamp in gone:
                self._sent.pop(lamp, None)
            self._mapper.resize(len(self._lamps))

    # ----- thread da captura -----
    def _block(self, gen, an, x):
        if gen != self._gen:
            return
        now = time.monotonic()
        self._last_block = now
        if float(np.dot(x, x)) > SOUND_MS * len(x):
            self._last_sound = now
        an.feed(x)

    def _capture_state(self, gen, state, detail):
        # "reopen" e passageiro e "stopped" quem avisa e o tick
        if gen == self._gen and state not in ("reopen", "stopped"):
            self._cap_state, self._cap_detail = state, detail or ""

    def _status(self, now):
        state, detail = self._cap_state, self._cap_detail
        if state is None:
            return "starting", ""
        if state == "running":
            return ("silent" if now - self._last_sound > SILENT_S else "running"), detail
        return state, detail

    # ----- thread do tick -----
    def _tick(self, halt, an):
        status = ("starting", "")  # antes de tudo: a captura pode abrir antes do primeiro tick
        _call(self._on_status, *status)
        while not halt.is_set():
            an.beat_event.wait(TICK_S)
            an.beat_event.clear()
            if halt.is_set():
                break
            now = time.monotonic()
            f = an.take()
            if now - self._last_block > STALE_S:  # captura parada: repouso calmo, sem congelar no ultimo quadro
                f = Features(wall=now)
            with self._lock:
                # lampada que saiu sozinha da musica (app do celular, desligada, conexao caiu) fica de fora
                self._drop([lamp for lamp in self._lamps if not _call(lambda: lamp.music_active)])
                out = self._mapper.tick(f, now)
                jump = self._mapper.style != "flow"
                sends = []
                for lamp, c in zip(self._lamps, out):
                    if needs_send(self._sent.get(lamp), c):
                        self._sent[lamp] = c
                        sends.append((lamp, c))
            for lamp, (h, s, v) in sends:
                _call(lamp.music_frame, h, s, v, jump=jump)  # nao bloqueia: a lampada guarda so o ultimo
            st = self._status(now)
            if st != status:
                status = st
                _call(self._on_status, *st)
            h, s, v = out[0]
            _call(self._on_frame, {"level": round(float(f.loud), 3), "bands": [round(float(b), 3) for b in f.bands],
                                   "beat": bool(f.beat), "h": h, "s": s, "v": v, "active": bool(f.gate)})
        _call(self._on_status, "stopped", "")


def _call(fn, *args, **kw):
    # erro de uma lampada ou da interface nao derruba o motor
    try:
        return fn(*args, **kw)
    except Exception:  # noqa: BLE001
        return None
