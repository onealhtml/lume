"""Modo musica: analisa o audio que o PC esta tocando e transforma em cor e brilho para as lampadas.
So numpy; o audio vem do loopback.py. Sem Qt: MusicSync avisa por callbacks chamados das suas threads.

A luz e reativa (como as caixas de som com LED): nunca dispara numa batida prevista. Varios detectores
rodam juntos (bumbo, notas do baixo, caixa/palma, ataques gerais), cada um contra o proprio fundo recente,
e o mais forte e regular "conduz": os golpes dele dao o flash. O bumbo manda sempre que esta presente; sem
bumbo o baixo, a caixa ou o violao assumem. A nota do baixo (classe de altura) escolhe a cor: a mesma nota
sempre a mesma cor, entao a luz "toca" a linha do baixo."""
import math
import os
import random
import sys
import threading
import time

# o analisador nao usa BLAS (rfft e o pocketfft): sem isso o OpenBLAS do numpy
# abre uma thread por nucleo no app da bandeja, paradas ate ele fechar
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np  # noqa: E402  depois do OPENBLAS_NUM_THREADS

import loopback

SUPPORTED = sys.platform == "win32" or sys.platform.startswith("linux")  # macOS nao tem loopback nativo
STYLES = ("pulse", "flow", "party")  # o mesmo que core.MUSIC_STYLES
DEBUG = os.environ.get("LUME_MUSIC_DEBUG") == "1"  # uma linha por golpe no stdout (o app congelado manda pro log)

# ----- analise lenta (quadro: mono, Hann, rfft de 2*hop amostras a cada hop de ~21 ms, tudo em dB) -----
# serve ao brilho: portao, AGC, envelopes, equilibrio, barras do visualizador
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
FLUX_FLOOR_DB = 50.0     # bandas cortadas em ref-50 dB: chiado nao gera subida

# ----- golpes (quadro fino: rfft de 4*fh amostras, ~21 ms, a cada fh, ~5.3 ms) -----
FINE_EDGES = (35.0, 150.0, 400.0, 2000.0, 8000.0, 16000.0)  # grave, corpo, medio, agudo (estalo/caixa), ar
LAG = 3                  # subida contra o quadro de 16 ms atras: o ataque do bumbo cabe, um inchaco de 100 ms nao
BODY_W = 0.6             # golpe grave = subida do grave + 0.6 * a do corpo (onde o bumbo comeca a varredura)
STATS_S = 1.5            # janela das estatisticas do limiar adaptativo de cada detector
LAYERS = ("kick", "bass", "snare", "broadband")
A_WEIGHT_DB = (-20.0, -6.0, 0.0, 1.0, -3.0)  # ponderacao A aproximada das 5 bandas finas: forca percebida
KICK, BASSN, SNARE, BROAD = range(4)
LOW_SHARE_DB = -8.0      # bumbo: grave >= 16 % da energia no golpe...
LOWMID_DB, LOWMID_S = 0.0, 2.0  # ...numa mistura em que o grave (2 s) nao fica abaixo do medio (fala fica ~10 dB)
LOWMID_MUSIC_DB = -12.0  # ...ou ate 12 dB abaixo quando o detector de pausas ja mediu 3 s e diz musica (rock, acustico,
                         # DnB e funk reais ficam entre -7 e 0 dB; so o equilibrio nao separa fala de rock)
PAUSE_DB, PAUSE_DECAY = 20.0, 7.0  # fala tem pausas: nivel 20 dB abaixo do pico recente (que cai 7 dB/s)...
PAUSE_S, PAUSE_MAX = 3.0, 0.15     # ...em mais de 15 % dos ultimos 3 s; baixo, caixa e violao so conduzem sem isso
PAUSE_K_S = 1.0          # a mesma medida em 1 s: musica que vira fala sem silencio perde logo o piso relaxado do bumbo
KICK_ODF_FRAC = 0.5      # ...subida >= metade da tipica dos ultimos bumbos (nota do baixo entre eles nao)...
KICK_LVL_DB = 3.0        # ...o grave chega a 3 dB do nivel tipico deles...
KICK_CLICK_FRAC = 0.35   # ...e, se os bumbos desta musica estalam (2-8 kHz), este tambem estala (baixo rolando nao)
KICK_CLICK_DB = 6.0
KICK_EARLY = 0.7         # golpe antes de 0.7 do intervalo comum entre bumbos (nota do baixo no contratempo)...
KICK_EARLY_FRAC = 0.75   # ...so e bumbo com subida de 75 % da tipica e grave a 1.5 dB do nivel tipico
KICK_LOUD_DB = 3.0       # grave 3 dB acima do tipico dispensa o estalo
KICK_K, KICK_DELTA = 0.7, 0.7  # o limiar do golpe grave e mais baixo que o dos outros: quem separa e a classificacao
KICK_WAIT = 3            # quadros que o golpe grave espera o pico antes de virar nota do baixo
# bumbo "de knock" (house/techno/EDM em camadas): o knock de 150-400 Hz chega 20-40 ms antes do sub, entao no
# ataque a parte do grave fica baixa (-10 a -16 dB). Golpe puxado pelo corpo (subida do corpo bem maior que a do
# grave) e julgado na energia de 35-400 Hz, contra o nivel tipico dos bumbos nessa mesma medida, e espera ate
# KNOCK_WAIT quadros (dispara no primeiro que passa); golpe puxado pelo sub segue o teste e o tempo de sempre
KNOCK_DB = 6.0
KNOCK_WAIT = 6
KNOCK_SUB_LVL = 4.0      # o sub do golpe de knock ja chegou a 4 dB do tipico dos bumbos ao disparar
# ...mas so nas musicas em que golpes puxados pelo knock se mostraram bumbos no rotulo tardio (corpo caindo
# >= 8 dB alem do sub em 60 ms): tanta, tom e nota de baixo com harmonicos tambem sobem mais no corpo, sem cair
KNOCK_LAB_DB, KNOCK_ON, KNOCK_A = 8.0, 0.5, 0.34
KICK_WARM_S = 1.5        # no inicio o nivel do grave e medido contra a referencia fixa do AGC
# rotulo tardio ("dispara rapido, rotula depois"): ~60 ms depois do golpe grave o corpo (150-400 Hz) do bumbo ja
# caiu bem mais que o sub (a varredura de altura do bumbo); a nota do baixo nao. So o golpe confirmado ensina os
# tipicos, o intervalo e a regularidade: nota do baixo tomada por bumbo custa um flash, nao estraga o modelo
LABEL_S, LABEL_PEAK_S = 0.06, 0.02  # o rotulo sai 60 ms depois; os picos sao os dos primeiros 20 ms
LABEL_MIN_DB = 8.0       # bumbos desta musica caem menos que isso (mediana): o rotulo nao separa, todo bumbo vale
LABEL_FRAC = 0.3         # com varredura forte, queda < 30 % da mediana: era nota do baixo
LABEL_RESCUE = 0.6       # golpe recusado com queda >= 60 % da mediana era bumbo (conta no intervalo)
KICK_TYP_A = (0.5, 0.25) # o tipico dos bumbos sobe rapido e desce devagar: segue os mais fortes
KICK_DECAY_DBPS = (1.5, 8.0)  # o nivel tipico cai devagar; sem bumbo ha 1.2 s, rapido (volume baixou, virada)
MIN_KICK_S = 0.15        # refratario do bumbo: 0.45 do intervalo mediano, entre 0.15 e 0.3 s
MIN_EV_S = 0.12          # refratario dos outros detectores
BASS_RATIO = 1.4         # nota do baixo: subida >= 1.4 x o limiar (ruido parado passaria raspando)
SNARE_DELTA, BROAD_DELTA = 1.2, 0.6   # x delta do bumbo (caixa soma agudo+medio; geral e a media de 5 bandas)
KICK_HOLD_S = 2.0        # o bumbo segue conduzindo por 2 s (ou 1.6 intervalos) depois do ultimo
KICK_MODE_S = 2.5        # com o baixo conduzindo, o bumbo assume depois de 3 bumbos em 2.5 s
DRIVE_EVERY_S = 0.25     # sem bumbo: a cada 0.25 s escolhe quem conduz
REG_WIN_S = 5.0          # regularidade: golpes dos ultimos 5 s que se repetem...
REG_PERIODS = np.geomspace(0.25, 1.5, 40)  # ...no melhor periodo entre 0.25 s e 1.5 s (mais rapido que 4/s a
                         # luz nao mostra sincronia e o chimbal nao deve conduzir)
REG_TOL = 0.03           # golpe "repetido": outro golpe um periodo antes, +-30 ms
REG_MIN, REG_MIN_N = 0.55, 8
REG_SLOW_N, REG_SLOW_S = 5, 0.55  # golpes lentos (mediana >= 0.55 s entre eles): bastam 5 na janela (dedilhado a 66 bpm)
REG_MAX_RATE = 6.0       # mais de 6 golpes por segundo nao conduz (a luz nao acompanha)
SAL_MIN = 0.05           # golpe que traz menos de 5 % da energia (ponderada) da mistura nao conduz (ruido, chiado)
SWITCH_RATIO = 1.3       # outro detector so assume com 30 % a mais (a luz nao fica trocando de dono)
CLAR_UP_S, CLAR_DOWN_S = 0.15, 1.5

# ----- nota do baixo (cor): rfft de ~170 ms a cada 2 quadros lentos (~43 ms), sinal decimado por 4 -----
NOTE_WIN_S = 0.17        # janela longa: perto de 41 Hz um semitom e ~2.5 Hz; os harmonicos resolvem
NOTE_DEC = 4             # 48 kHz -> 12 kHz pela media de 4 amostras: so interessa ate ~1.2 kHz
NOTE_EVERY = 2           # a cada 2 quadros lentos (~43 ms)
NOTE_RESET = 0.5         # golpe grave (bumbo ou nota): a evidencia antiga vale isso (a nota nova ganha antes)
NOTE_MIDI = (24, 60)     # candidatas C1..B3 (3 oitavas, dobradas nas 12 classes: oitava errada nao muda a cor)
NOTE_FMAX = 1200.0
NOTE_HARM = (0.4, 1.0, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5)  # peso de cada harmonico: o fundamental vale pouco
                         # (o bumbo e quase so fundamental; baixo, 808 com drive e piano tem harmonicos)
NOTE_COMP = 0.15         # potencia ** 0.15: harmonico fraco ainda soma
NOTE_TAU = 0.04          # a evidencia de cada classe e quase so a da ultima analise (a janela ja e longa)
NOTE_HYST, NOTE_SURE = 1.1, 1.15  # outra nota assume com 10 % a mais em 2 analises seguidas (15 %: na hora)
NOTE_KICK = (0.03, 0.15, 0.3)    # janela cheia de bumbo (30-150 ms depois dele) vale so 30 %
NOTE_BANDS = 6           # bandas do visualizador ate ~330 Hz: onde ficam o baixo e os primeiros harmonicos
NOTE_LOW_DB = -8.0       # menos de ~16 % da mistura ali: sem linha de baixo (a fala fica ~-10 dB), a cor nao segue
NOTE_CONF_S = 1.0        # confianca: sobe e desce em ~1 s (uma falha curta nao derruba a cor da nota)
NOTE_RATE_S, NOTE_RATE_N = 2.0, 7  # 7 trocas de nota em 2 s: fala ou vibrato, nao linha de baixo

# ----- luz -----
FLOOR_V = 10             # %: perto de 0 as lampadas parecem apagadas (tipo B: minimo 10/1000)
GAMMA = 1.6              # contraste percebido: v = piso + faixa * y**1.6
FLASH_DECAY_S = 0.20     # depois da queda o resto do flash some assim
HOLD_S = 0.08            # o flash fica ~80 ms no pico (perto do brilho percebido maximo) e cai num quadro rapido
FALL_KEEP = {"pulse": 0.0, "flow": 0.0, "party": 0.15}  # quanto do flash sobra na queda
FALL_JUMP = {"pulse": False, "flow": False, "party": True}  # queda em degrau (party) ou em fade de ~300 ms
FLOW_FLASH = 0.5         # flow: o golpe so incha o brilho
FLOW_GLIDE = 240.0       # flow: teto de graus/s da matiz (o empurrao mais forte gira ate isso)
FLOW_IDLE_DPS = 2.0      # flow: a cor gira sempre para frente, 2 graus/s no calmo...
FLOW_LOUD_DPS = 8.0      # ...ate +8 com a musica alta e ritmada (fala fica perto dos 2)
FLOW_BEAT_DEG = 24.0     # cada golpe de quem conduz empurra ate 24 graus: 2 golpes/s ~ 50 graus/s
FLOW_NOTE_DEG = 30.0     # nota nova do baixo empurra 30 graus (a troca de nota aparece na cor)
FLOW_PUSH_S = 0.4        # o empurrao sai em fade de ~0.4 s: gira mais rapido, nunca salta
FLOW_PUSH_MAX = 60.0
PARTY_GAP_S = 0.2        # party sem nota: no maximo 5 cores por segundo
PARTY_WHITE = 35         # party: no pico do flash a cor fica mais clara (o RGB todo acende)
BREATH_RISE = 1.0        # o brilho fora dos golpes sobe no maximo 100 %/s (respira, nao pisca)...
BREATH_TOP = 0.6         # ...e fica abaixo de 60 % da faixa: o flash sempre tem para onde subir
DRIVEN_TOP = 0.3         # com alguem conduzindo o fundo fica mais baixo: mais contraste no golpe
IDLE_S = 0.10            # antes do golpe esperado nada sai: a lampada esta livre quando ele chega
NOTE_HUE = 30.0          # graus por semitom (party e flow): cada nota a sua cor
PULSE_NOTE_DEG = 20.0    # pulse: a nota so desloca a cor do usuario ate 20 graus
NOTE_ON = 0.5            # confianca minima para a nota mandar na cor
NOTE_STALE_S = 8.0       # party: baixo na mesma nota ha 8 s -> as cores voltam a trocar nos golpes
GOLDEN = 137.508         # angulo de ouro: cores seguidas do party bem diferentes

# ----- MusicSync -----
TICK_S = 1 / 15
GAP_BEAT = 0.06          # o mesmo que core.MUSIC_GAP_BEAT: quadro de golpe sai 60 ms depois do anterior...
GAP_QUICK = 0.09         # ...a queda do flash e a cor da nota (core.MUSIC_GAP_QUICK) 90 ms...
GAP_OTHER = 0.25         # ...e os outros (core.MUSIC_GAP_LIVE) 0.25 s: a lampada fica livre quando o golpe chega
SILENT_S = 2.0           # tanto tempo sem som: status "silent"
SOUND_MS = 1e-8          # media do quadrado acima disso (-80 dBFS) e som tocando
STALE_S = 0.5            # captura sem blocos por mais que isso (erro, sem dispositivo): lampada em repouso
# relogio de tudo que se compara no caminho da musica (o mesmo que core.CLOCK; aqui sem importar o core: o
# simulador carrega so este modulo). No Windows com Python < 3.13 o time.monotonic() anda de 15.6 em 15.6 ms
CLOCK = time.perf_counter


def _lerp3(s, a0, a50, a100):
    s = min(max(float(s), 0.0), 100.0)
    return a0 + (a50 - a0) * s / 50 if s <= 50 else a50 + (a100 - a50) * (s - 50) / 50


class SensParams:
    """O que o botao de sensibilidade (0-100) muda; valores em 0 / 50 / 100."""
    __slots__ = ("agc_window_db", "beat_k", "beat_delta_db", "gate_open_dbfs", "punch_db", "depth", "flash")

    def __init__(self, s):
        self.agc_window_db = _lerp3(s, 32, 24, 16)    # dB abaixo da referencia que viram 0..1
        self.beat_k = _lerp3(s, 2.0, 1.6, 1.2)         # limiar = media + k * desvio + delta
        self.beat_delta_db = _lerp3(s, 6.5, 5.0, 3.5)  # subida minima do golpe grave
        self.gate_open_dbfs = _lerp3(s, -54, -62, -68)
        self.punch_db = _lerp3(s, 14.0, 10.0, 6.0)     # grave acima da media que vale punch = 1
        self.depth = _lerp3(s, 0.55, 0.8, 1.0)         # quanto da faixa de brilho a musica usa
        self.flash = _lerp3(s, 0.6, 0.9, 1.0)          # forca do flash no golpe


_FEATURES = {"wall": 0.0, "active": 0.0, "gate": False, "loud": 0.0, "bass": 0.0, "mid": 0.0, "treble": 0.0,
             "punch": 0.0, "balance": 0.0, "bands": (0.0,) * N_BANDS, "beat": False, "beats": 0,
             "beat_strength": 0.0, "beat_wall": 0.0, "layer": "", "sub": False, "sub_strength": 0.0,
             "sub_layer": "", "onset": False, "onset_strength": 0.0, "driver": "", "tempo_bpm": 0.0,
             "period": 0.0, "clarity": 0.0, "locked": False, "next_beat": None, "note": -1, "note_conf": 0.0,
             "note_n": 0}


class Features:
    """Saida do analisador. wall: relogio do quadro mais novo; envelopes 0-1; balance -1 grave .. +1 agudo.
    beat: golpe de quem conduz (layer: kick/bass/snare/broad), beat_wall: quando foi detectado;
    sub: golpe de outro detector; driver: quem conduz ("" = ninguem: fala, ambiente);
    clarity: 0 fala/ambiente .. 1 ritmo claro; tempo_bpm e period: de quem conduz; next_beat: quando o proximo
    golpe dele deve vir (so para deixar a lampada livre; nada dispara nisso); note: classe de altura do baixo
    (0 = do, -1 = nenhuma), note_conf 0-1, note_n conta as trocas de nota."""
    __slots__ = tuple(_FEATURES)

    def __init__(self, **kw):
        for k, v in _FEATURES.items():
            setattr(self, k, kw.get(k, v))

    def copy(self, **kw):
        return Features(**{**{k: getattr(self, k) for k in _FEATURES}, **kw})


def _typ(old, new):
    return old + KICK_TYP_A[0 if new > old else 1] * (new - old)


def _regularity(ev):
    """Quanto os golpes (t, peso) se repetem: parte (pelo peso) dos golpes que tem outro golpe exatamente um
    periodo antes, no melhor periodo entre 0.25 e 1.5 s, acima do acaso. Padrao sincopado que se repete a cada
    compasso (3-3-2, tamborzao) conta como regular; golpes soltos nao. 1 = tudo se repete, 0 = acaso."""
    tt = np.fromiter((e[0] for e in ev), float, len(ev))
    w = np.fromiter((e[1] for e in ev), float, len(ev))
    d = tt[:, None] - tt[None, :]
    near = np.abs(d[None, :, :] - REG_PERIODS[:, None, None]) < REG_TOL
    has = near.any(axis=2)                          # periodos x golpes
    can = tt[None, :] - REG_PERIODS[:, None] >= tt[0] - REG_TOL  # o golpe de um periodo antes caberia na janela
    hit = has & can
    num = hit @ w
    den = can @ w
    ok = (den > 0.3 * w.sum()) & (hit.sum(axis=1) >= 4)  # pelo menos 4 golpes repetidos
    if not ok.any():
        return 0.0
    r = np.where(ok, num / np.maximum(den, EPS), 0.0)
    k = int(np.argmax(r))
    chance = min(0.9, 2 * REG_TOL * len(tt) / max(0.5, tt[-1] - tt[0]))
    return max(0.0, (float(r[k]) - chance) / (1.0 - chance))


class MusicAnalyzer:
    """feed() na thread de captura (qualquer tamanho de bloco); take() na thread do tick.
    beat_event acende a cada golpe de quem conduz: o tick espera nele e manda o flash na hora."""

    def __init__(self, sample_rate=48000, sensitivity=50, clock=CLOCK):
        self.sr = sr = int(sample_rate)
        self.hop = 512 if sr <= 26000 else 1024 if sr <= 52000 else 2048   # ~21 ms
        self.nfft = n = 2 * self.hop
        self.dt = dt = self.hop / sr
        self.fh = fh = self.hop // 4                                     # ~5.3 ms
        self.fn = fn = 4 * fh
        self.fdt = fdt = fh / sr
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
        # quadro fino
        self._fwin = np.hanning(fn).astype(np.float32)
        self._fnorm = 2.0 / (fn * float(np.sum(self._fwin.astype(np.float64) ** 2)))
        fb = [max(1, int(round(f * fn / sr))) for f in FINE_EDGES]
        fb[-1] = min(fb[-1], fn // 2)
        for i in range(1, len(fb)):
            fb[i] = max(fb[i], fb[i - 1] + 1)
        self._fb0, self._fb1 = fb[0], fb[-1]
        self._fidx = np.array(fb[:-1]) - fb[0]
        self._aw = 10.0 ** (np.array(A_WEIGHT_DB) / 10.0)
        # nota do baixo: janela de ~170 ms (potencia de 2), decimada; matriz soma os harmonicos de cada candidata
        self.pn = pn = max(n, 1 << int(round(math.log2(NOTE_WIN_S * sr))))
        nd = pn // NOTE_DEC
        fsd = sr / NOTE_DEC
        self._pwin = np.hanning(nd).astype(np.float32)
        nb = min(nd // 2, int(NOTE_FMAX * nd / fsd) + 2)
        mat = np.zeros((NOTE_MIDI[1] - NOTE_MIDI[0], nb), np.float32)
        for r, m in enumerate(range(*NOTE_MIDI)):
            f0 = 440.0 * 2.0 ** ((m - 69) / 12.0)
            for h, w in enumerate(NOTE_HARM, 1):
                pos = f0 * h * nd / fsd
                if pos >= nb - 1:
                    break
                i = int(pos)
                mat[r, i] += w * (1.0 - (pos - i))
                mat[r, i + 1] += w * (pos - i)
        self._pmat, self._pnb = mat, nb
        self._pfold = (len(mat) // 12, 12)   # NOTE_MIDI comeca num do: cada coluna e uma classe

        def a(tau, step=dt):  # coeficiente de suavizacao por quadro
            return 1.0 - math.exp(-step / tau)
        self._a_gate = a(0.1)
        self._a_act_up, self._a_act_down = a(ACT_UP_S), a(ACT_DOWN_S)
        self._a_att, self._a_rel, self._a_fast = a(AGC_ATTACK_S), a(AGC_RELEASE_S), a(AGC_FAST_RELEASE_S)
        self._a_env = [a(r) for r in ENV_RELEASE_S]
        self._a_balavg, self._a_bal = a(BAL_AVG_S), a(BAL_SMOOTH_S)
        self._a_pbase, self._a_punch = a(PUNCH_BASE_S), a(PUNCH_RELEASE_S)
        self._a_bar = a(BAR_RELEASE_S)
        self._a_stats = a(STATS_S, fdt)
        self._a_clar_up, self._a_clar_down = a(CLAR_UP_S, fdt), a(CLAR_DOWN_S, fdt)
        self._a_lowmid = a(LOWMID_S, fdt)
        self._a_note = a(NOTE_TAU, NOTE_EVERY * dt)
        self._a_nconf = a(NOTE_CONF_S, NOTE_EVERY * dt)
        self._lowmid = 10 ** (LOWMID_DB / 10)
        self._lowmid_music = 10 ** (LOWMID_MUSIC_DB / 10)
        self._a_pause = a(PAUSE_S, fdt)
        self._a_pause_k = a(PAUSE_K_S, fdt)
        self._pause_step = PAUSE_DECAY * fdt
        self._peak_step = PEAK_DECAY_DBPS * dt
        self._bar_step = BAR_DECAY_DBPS * dt
        self._kdecay = [r * fdt for r in KICK_DECAY_DBPS]

        self._lock = threading.Lock()
        self.beat_event = threading.Event()
        self._reset_state()

    def set_sensitivity(self, s):
        self.sens = SensParams(s)

    def _kick_reset(self):
        self._klvl = None           # nivel, subida, estalo e queda do corpo tipicos dos bumbos desta musica
        self._klvl4 = None          # o nivel tipico medido em 35-400 Hz (golpes puxados pelo knock)
        self._kn = 0.0              # evidencia de bumbo de knock nesta musica (0..1)
        self._kodf = 0.0
        self._khigh = 0.0
        self._kdrops = []           # quedas do corpo dos ultimos bumbos (rotulo tardio)
        self._klab = -1e9           # ultimo bumbo confirmado
        self._iki = 0.0             # intervalo comum (quartil de baixo) entre bumbos

    def _reset_state(self):
        self._t = 0.0               # relogio do audio (fim do quadro fino mais novo)
        self._nf = 0                # quadros finos ja feitos (a cada 4, um quadro lento; a cada 8, a nota)
        self._tail = np.zeros(self.pn - self.fh, np.float32)  # amostras antes do proximo pedaco
        self._stage = np.zeros(0, np.float32)
        self._lg = SILENCE_DB
        self._gate = False
        self._opened = False        # o quadro fino abriu o portao; o lento ainda inicia o AGC
        self._open_t = -1e9         # quando o portao abriu
        self._ref0, self._ref_t = 0.0, -1e9  # referencia do AGC no inicio (fixa no primeiro segundo)
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
        # golpes
        self._fprev = None          # niveis finos dos ultimos LAG quadros
        self._elow = self._emid = 0.0  # energia media (2 s) do grave e do medio
        self._lpk = SILENCE_DB      # pico do nivel geral, caindo devagar
        self._pause = 0.0           # parte recente do tempo em pausa (fala)
        self._pause_k = 0.0         # o mesmo, rapido (so para o piso relaxado do bumbo)
        self._mu = [0.0, 0.0, 0.0]  # estatisticas dos limiares: grave, caixa, geral
        self._var = [0.0, 0.0, 0.0]
        self._last = [-1e9] * 4     # ultimo golpe de cada detector
        self._ev = ([], [], [], [])  # golpes recentes (t, peso) de cada detector
        self._cand = None           # golpe grave esperando o pico: [t, subida, nivel, parte do grave, quadros]
        self._lab = []              # golpes graves esperando o rotulo tardio
        self._eah = np.zeros((LAG, 5))  # energias ponderadas dos ultimos quadros finos
        self._sal = (0.0, 0.0, 0.0)
        self._d3h = [0.0] * 4
        self._kick_reset()
        self._driver = None
        self._dscore = 0.0
        self._dlost = 0.0
        self._next_drive = 0.0
        self._clar = 0.0
        self._lowsh = -99.0         # grave - geral (dB) no ultimo quadro lento
        self._chroma = np.zeros(12)  # evidencia de cada classe de altura (media curta)
        self._note = -1
        self._ncand, self._ncount = -1, 0
        self._nconf = 0.0
        self._note_n = 0
        self._note_ts = []          # quando a nota trocou (ultimos segundos)
        self._out = Features()
        self._pkey, self._pcache = None, (0.0, None)  # periodo e ultimo golpe de quem conduz (so muda com golpe)
        self._acc = None            # acumulado desde o ultimo take()
        self._hit = None            # golpes deste feed(): [layer, forca], [sub layer, forca], onset
        self._sub = None
        self._onset = 0.0

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
        fh, n, pn = self.fh, self.nfft, self.pn
        k = stage.size // fh
        if k == 0:
            self._stage = stage.copy()
            return
        buf = np.concatenate((self._tail, stage[:k * fh]))
        self._stage = stage[k * fh:].copy()
        self._tail = buf[-(pn - fh):].copy()
        # todos os quadros finos do bloco numa rfft so
        ends = (pn - fh) + fh * np.arange(1, k + 1)
        fr = buf[(ends - self.fn)[:, None] + np.arange(self.fn)[None, :]] * self._fwin
        X = np.fft.rfft(fr, axis=1)
        p = X.real * X.real + X.imag * X.imag
        e = np.add.reduceat(p[:, self._fb0:self._fb1], self._fidx, axis=1) * self._fnorm
        lv = 10.0 * np.log10(e + EPS)
        tot = 10.0 * np.log10(e.sum(axis=1) + EPS)
        share = lv[:, 0] - tot
        lv4 = 10.0 * np.log10(e[:, 0] + e[:, 1] + EPS)   # grave + corpo (35-400 Hz)
        # forca de cada golpe: energia (ponderada como o ouvido) que entrou nos ultimos 16 ms, em fracao da
        # mistura toda. O chimbal regular quase nao soma; a nota do baixo, o violao e a caixa somam
        ea = e * self._aw
        eh = np.concatenate((self._eah, ea))
        rise = np.maximum(ea - eh[:k], 0.0)
        ta = ea.sum(axis=1) + EPS
        sal = np.stack((rise[:, 0] + rise[:, 1], rise[:, 2] + rise[:, 3], rise.sum(axis=1)), axis=1) / ta[:, None]
        self._eah = eh[-LAG:]
        self._hit = self._sub = None
        self._onset = 0.0
        pk = None  # picos dos envelopes entre dois take()
        for i in range(k):
            self._nf += 1
            self._t += self.fdt
            if self._nf % 4 == 0:  # quadro lento, terminando no mesmo ponto
                self._process(buf[ends[i] - n:ends[i]])
                pk = self._peaks(pk)
                if self._nf % (4 * NOTE_EVERY) == 0:
                    self._pitch(buf[ends[i] - pn:ends[i]])
            self._sal = sal[i]
            self._fine(lv[i], share[i], e[i, 0], e[i, 2], tot[i], float(lv4[i]))
        self._publish(pk)

    def _process(self, frame):
        dt, sp = self.dt, self.sens
        X = np.fft.rfft(frame * self._win)
        p = X.real * X.real + X.imag * X.imag
        bp = np.add.reduceat(p[self._b0:self._b1], self._idx) * self._norm  # potencia das 16 bandas
        lb = 10.0 * np.log10(bp + EPS)
        g = np.add.reduceat(bp, self._grp_idx)
        tot = float(g.sum())
        lvl = [10.0 * math.log10(tot + EPS), 10.0 * math.log10(g[0] + EPS),
               10.0 * math.log10(g[1] + EPS), 10.0 * math.log10(g[2] + EPS)]

        self._lowsh = 10.0 * math.log10(float(bp[:NOTE_BANDS].sum()) + EPS) - lvl[0]
        # portao de ruido em dBFS absoluto (loopback nao tem ruido de microfone), com histerese e espera;
        # abre na hora (o nivel sobe sem suavizar): o primeiro bumbo da musica ja conta
        x0 = max(lvl[0], -100.0)
        self._lg = x0 if x0 > self._lg else self._lg + (x0 - self._lg) * self._a_gate
        if self._gate and not self._opened:
            self._below = self._below + dt if self._lg < sp.gate_open_dbfs - GATE_HYST_DB else 0.0
            if self._below >= GATE_HOLD_S:  # silencio de verdade: a proxima musica aprende o bumbo dela
                self._gate, self._silent = False, 0.0
                self._kick_reset()
        elif self._gate or self._lg > sp.gate_open_dbfs:
            if not self._gate:
                self._open_t = self._t
            self._gate, self._below, self._opened = True, 0.0, False
            if self._ref is None or self._silent > GATE_RESET_S:
                self._ref = [lvl[0] + 3.0] + [max(lvl[i], lvl[0] - AGC_MAX_BOOST_DB[i - 1]) + 3.0 for i in (1, 2, 3)]
                self._ref0, self._ref_t = self._ref[0], self._t
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

    def _peaks(self, pk):
        e = self._env
        if pk is None:
            return [e[0], e[1], e[2], e[3], self._punch, self._bars]
        return [max(pk[0], e[0]), max(pk[1], e[1]), max(pk[2], e[2]), max(pk[3], e[3]), max(pk[4], self._punch),
                np.maximum(pk[5], self._bars)]

    # ----- nota do baixo -----
    def _pitch(self, frame):
        """Classe de altura da nota mais grave por soma de harmonicos. Sem baixo, fala ou logo depois do bumbo
        (o bumbo e uma nota grave curta) a evidencia nao entra ou entra pouco."""
        t = self._t
        # nota trocando mais de 3 vezes por segundo nao e linha de baixo (voz falando, vibrato): a cor nao segue
        nt = self._note_ts
        while nt and t - nt[0] > NOTE_RATE_S:
            del nt[0]
        ok = self._gate and self._pause < PAUSE_MAX and self._lowsh >= NOTE_LOW_DB and len(nt) < NOTE_RATE_N
        self._nconf += ((1.0 if ok else 0.0) - self._nconf) * self._a_nconf
        if not ok:
            self._ncand = -1
            return
        dk = t - self._last[KICK]
        w = NOTE_KICK[2] if NOTE_KICK[0] <= dk < NOTE_KICK[1] else 1.0
        X = np.fft.rfft(frame.reshape(-1, NOTE_DEC).mean(axis=1) * self._pwin)
        p = X[:self._pnb]
        p = p.real * p.real + p.imag * p.imag
        c = (self._pmat @ (p + EPS) ** NOTE_COMP).reshape(self._pfold).max(axis=0)
        c /= c.max() + EPS
        e = self._chroma
        e += (w * self._a_note) * (c - e)
        b = int(np.argmax(e))
        cur = self._note
        if cur >= 0 and (b == cur or e[b] <= NOTE_HYST * e[cur]):
            self._ncand = -1
            return
        self._ncount = self._ncount + 1 if b == self._ncand else 1
        self._ncand = b
        if self._ncount >= 2 or cur < 0 or e[b] > NOTE_SURE * e[cur]:
            self._note, self._ncand = b, -1
            self._note_n += 1
            self._note_ts.append(t)

    # ----- golpes -----
    def _thr(self, i, x, delta, ks=1.0):
        """Limiar adaptativo (media + k*desvio + delta de ~1.5 s) do detector i; atualiza as estatisticas.
        Valores acima do limiar entram cortados e 4x mais devagar: golpe denso nao sobe o proprio limiar."""
        mu, var = self._mu[i], self._var[i]
        thr = mu + ks * self.sens.beat_k * math.sqrt(var) + delta
        a = self._a_stats
        if x > thr:
            x, a = thr, a * 0.25
        d = x - mu
        self._mu[i] = mu + a * d
        self._var[i] = var + a * (d * d - var)
        return thr

    def _fine(self, lv, share, elow, emid, tot, lv4):
        t, gate = self._t, self._gate
        if not gate and tot > self.sens.gate_open_dbfs + GATE_HYST_DB:
            # golpe forte saindo do silencio: abre ja (o quadro lento so viria ~20 ms depois do ataque)
            self._gate = gate = self._opened = True
            self._below, self._open_t = 0.0, t
        self._elow += (elow - self._elow) * self._a_lowmid
        self._emid += (emid - self._emid) * self._a_lowmid
        self._lpk = max(tot, self._lpk - self._pause_step)
        if gate:
            pz = 1.0 if tot < self._lpk - PAUSE_DB else 0.0
            self._pause += (pz - self._pause) * self._a_pause
            self._pause_k += (pz - self._pause_k) * self._a_pause_k
        floor = (self._ref[0] if self._ref else -60.0) - FLUX_FLOOR_DB
        lv = np.maximum(lv, floor)
        hist = self._fprev
        if hist is None:
            hist = self._fprev = [lv] * LAG
        d = np.maximum(lv - hist[0], 0.0)
        hist.append(lv)
        del hist[0]
        d0, d1, d2, d3, d4 = d.tolist()
        d3h = self._d3h  # estalos recentes: com sub-grave alto o grave do bumbo sobe uns ms depois do estalo
        d3h.append(d3)
        del d3h[0]
        # nivel do grave contra o AGC (volume mudou, o tipico segue); no primeiro segundo o AGC ainda sobe e o
        # nivel tipico do primeiro bumbo ficaria alto demais: ali a referencia e a do inicio, fixa
        if self._ref and t - self._ref_t < KICK_WARM_S:
            ref0 = self._ref0
        else:
            if self._ref_t > -1e9 and self._ref:
                if self._klvl is not None:  # fim do aquecimento: o tipico passa para a referencia que anda
                    self._klvl += self._ref0 - self._ref[0]
                    self._klvl4 += self._ref0 - self._ref[0]
                self._ref_t = -1e9
            ref0 = self._ref[0] if self._ref else 0.0
        low = float(lv[0]) - ref0
        low4 = lv4 - ref0
        delta = self.sens.beat_delta_db
        k_odf = d0 + BODY_W * d1
        s_odf = d3 + 0.5 * d2 if lv[3] > lv[4] - 3.0 else 0.0   # caixa e palma: ruido de 2-8 kHz (chimbal e mais agudo)
        b_odf = (d0 + d1 + d2 + d3 + d4) * 0.2
        tk = self._thr(0, k_odf, KICK_DELTA * delta, KICK_K)
        ts = self._thr(1, s_odf, SNARE_DELTA * delta)
        tb = self._thr(2, b_odf, BROAD_DELTA * delta)

        # nivel tipico dos bumbos cai devagar; sem bumbo ha 1.2 s, rapido (volume baixou, fim da parte com bumbo)
        if self._klvl is not None:
            kd = self._kdecay[1 if t - self._last[KICK] > 1.2 else 0]
            self._klvl -= kd
            self._klvl4 -= kd
            self._kodf *= 1.0 - 0.3 * self.fdt
            self._khigh *= 1.0 - 0.3 * self.fdt
        lab = self._lab
        if lab:
            l0, l1, l3 = float(lv[0]), float(lv[1]), float(lv[3])
            for r in lab:  # picos logo depois do golpe (o nivel tipico usa o pico do grave)
                if t - r[0] <= LABEL_PEAK_S:
                    r[3], r[4], r[5], r[7] = max(r[3], l0), max(r[4], l1), max(r[5], low), max(r[7], l3)
                    r[8] = max(r[8], low4)
            while lab and t - lab[0][0] >= LABEL_S:
                r = lab.pop(0)
                self._label(r, (r[4] - l1) - (r[3] - l0))

        # golpe grave: espera ate KICK_WAIT quadros pelo pico; bumbo se for forte como os bumbos recentes
        c = self._cand
        if c is None:
            refr = min(0.3, max(MIN_KICK_S, 0.45 * self._iki))
            if gate and k_odf > tk and t - self._last[KICK] >= refr:
                c = self._cand = [t, k_odf, low, share, 0, tk, max(d3h), float(self._sal[0]), low4, lv4 - tot, d0, d1]
        else:
            c[1], c[2], c[3], c[4], c[6] = max(c[1], k_odf), max(c[2], low), max(c[3], share), c[4] + 1, max(c[6], d3)
            c[7] = max(c[7], float(self._sal[0]))
            c[8], c[9], c[10], c[11] = max(c[8], low4), max(c[9], lv4 - tot), max(c[10], d0), max(c[11], d1)
        if c is not None:
            kl, kh = self._klvl, self._khigh
            # antes do intervalo comum entre bumbos (tipico: nota do baixo no contratempo) so passa golpe bem forte
            early = c[0] - self._last[KICK] < KICK_EARLY * self._iki
            # sem estalo so passa grave bem acima do tipico (bumbo ao vivo varia; nota do baixo nao fica acima)
            # mistura com pouco grave (rock, acustico): o piso relaxado so depois de o detector de pausas medir
            # PAUSE_S desde que o portao abriu (antes disso o _pause ainda perto de 0 nao garante "musica, nao fala")
            lowmid = self._elow >= self._emid * self._lowmid or (
                self._pause < PAUSE_MAX and self._pause_k < PAUSE_MAX and t - self._open_t >= PAUSE_S
                and self._elow >= self._emid * self._lowmid_music)
            # puxado pelo knock: parte e nivel em 35-400 Hz (o sub ainda nao chegou)
            knock = self._kn >= KNOCK_ON and c[11] >= c[10] + KNOCK_DB
            if knock and kl is not None:
                # ...e o sub ja comecou a chegar (caixa e tom com corpo forte nao trazem sub)
                knock_sub = c[2] >= kl - KNOCK_SUB_LVL
                kl, lvl, sh = self._klvl4, c[8], (c[9] if knock_sub else -99.0)
            else:
                lvl, sh = c[2], c[3]
            if sh >= LOW_SHARE_DB and lowmid and (kl is None or (
                    lvl >= kl - KICK_LVL_DB * (0.5 if early else 1.0)
                    and c[1] >= (KICK_EARLY_FRAC if early else KICK_ODF_FRAC) * self._kodf
                    and (kh < KICK_CLICK_DB or c[6] >= KICK_CLICK_FRAC * kh
                         or (lvl >= kl + KICK_LOUD_DB and c[1] >= KICK_EARLY_FRAC * self._kodf)))):
                self._cand = None
                self._kick(t, c)
            elif c[4] >= (KNOCK_WAIT if knock else KICK_WAIT):  # espera o estalo inteiro (o sub as vezes chega antes)
                self._cand = None
                self._pend(BASSN, c)
                # nota do baixo: subida bem acima do limiar (o grave de um ruido parado oscila e passaria raspando)
                if t - self._last[BASSN] >= MIN_EV_S and c[1] >= BASS_RATIO * c[5]:
                    self._event(BASSN, t, min(1.0, (c[1] - c[5]) / max(1.0, c[5])), c[1] / c[5], sal=c[7])
        if gate and s_odf > ts and t - self._last[SNARE] >= MIN_EV_S:
            self._event(SNARE, t, min(1.0, (s_odf - ts) / max(1.0, ts)), s_odf / ts, sal=float(self._sal[1]))
        if gate and b_odf > tb and t - self._last[BROAD] >= MIN_EV_S:
            self._event(BROAD, t, min(1.0, (b_odf - tb) / max(1.0, tb)), b_odf / tb, sal=float(self._sal[2]))
            self._onset = max(self._onset, min(1.0, (b_odf - tb) / max(1.0, tb)))

        if t >= self._next_drive:
            self._next_drive = t + DRIVE_EVERY_S
            self._drive(t)
        target = 0.0 if self._driver is None else 1.0 if self._driver == KICK else min(1.0, 0.4 + self._dscore)
        self._clar += (target - self._clar) * (self._a_clar_up if target > self._clar else self._a_clar_down)

    def _kick(self, t, c):
        st = 1.0 if not self._kodf else min(1.0, 0.5 + 0.5 * c[1] / max(1e-6, self._kodf / 0.85))
        if self._driver is None:  # sem ninguem conduzindo o bumbo assume na hora; de outro, quando ficar regular
            self._driver, self._dscore, self._dlost = KICK, 1.0, 0.0
        self._pend(KICK, c)
        self._event(KICK, t, st, c[1] / c[5], keep=False)  # entra na regularidade so depois do rotulo

    def _pend(self, kind, c):
        """Golpe grave (bumbo ou recusado) esperando o rotulo tardio: picos do sub, do corpo e do estalo."""
        h = self._fprev
        self._lab.append([c[0], kind, c, max(x[0] for x in h), max(x[1] for x in h), c[2], float(h[0][3]),
                          max(x[3] for x in h), c[8]])

    def _label(self, r, drop):
        """drop: quanto o corpo caiu alem do sub 60 ms depois (a varredura do bumbo eletronico)."""
        t0, kind, c, kpk = r[0], r[1], r[2], r[5]
        if c[11] >= c[10] + KNOCK_DB:  # golpe puxado pelo corpo: o rotulo diz se esta musica tem bumbo de knock
            self._kn += KNOCK_A * (1.0 - self._kn) if drop >= KNOCK_LAB_DB else -0.5 * KNOCK_A * self._kn
        kd = self._kdrops
        med = float(np.median(kd)) if len(kd) >= 4 else 0.0
        sweep = med >= LABEL_MIN_DB  # os bumbos desta musica tem varredura forte (eletronico)
        if kind != KICK:
            # recusado (estalo fraco, cedo demais) mas com a queda de um bumbo: so entra no intervalo entre
            # bumbos (sem isso, preso em meio tempo, a regra do "cedo demais" recusaria um bumbo sim, outro nao)
            if sweep and drop >= LABEL_RESCUE * med and t0 - self._klab > 0.15:
                self._klab = t0
                self._ev_add(KICK, t0, c[1] / c[5], c[7])
                self._iki_update()
            return
        kd.append(drop)
        if len(kd) > 12:
            del kd[0]
        if sweep and drop < LABEL_FRAC * med:  # um golpe sem queda era nota do baixo: nao ensina nada
            self._ev_add(BASSN, t0, c[1] / c[5], c[7])
            return
        self._klab = t0
        if t0 - self._open_t > 0.1:
            self._kodf = c[1] if not self._kodf else _typ(self._kodf, c[1])
            hi = min(max(c[6], r[7] - r[6]), 30.0)  # o estalo inteiro, ate ~20 ms depois
            self._khigh = hi if self._klvl is None else _typ(self._khigh, hi)
        elif not self._kodf:  # saindo do silencio tudo sobe demais: subida limitada e sem estalo aprendido
            self._kodf = min(c[1], 2.5 * c[5])
        self._klvl4 = r[8] if self._klvl is None else _typ(self._klvl4, r[8])
        self._klvl = kpk if self._klvl is None else _typ(self._klvl, kpk)
        self._ev_add(KICK, t0, c[1] / c[5], c[7])
        self._iki_update()

    def _iki_update(self):
        """Intervalo comum (quartil de baixo) entre bumbos: so ajusta refratario e limiar, nunca dispara."""
        ev = self._ev[KICK]
        tt = [e[0] for e in ev[-8:]]
        ik = [y - x for x, y in zip(tt, tt[1:]) if 0.1 < y - x < 2.0]
        if ik:
            self._iki = float(np.percentile(ik, 25))

    def _ev_add(self, layer, t, weight, sal=0.0):
        ev = self._ev[layer]
        ev.append((t, min(3.0, weight), sal))
        if len(ev) > 48:
            del ev[:16]

    def _event(self, layer, t, strength, weight, keep=True, sal=0.0):
        self._last[layer] = t
        if layer <= BASSN:
            self._chroma *= NOTE_RESET
        if keep:
            self._ev_add(layer, t, weight, sal)
        strength = max(0.15, strength)
        if layer == self._driver or (layer == KICK and self._driver == BASSN):
            if self._hit is None or strength > self._hit[1]:
                self._hit = [layer, strength]
        elif self._sub is None or strength > self._sub[1]:
            self._sub = [layer, strength]

    def _drive(self, t):
        """Quem conduz: o bumbo enquanto existe; senao o grupo mais forte e regular, com histerese. Sem bumbo
        os golpes graves (notas do baixo e o que parecer bumbo) sao um grupo so: a luz segue a linha do baixo."""
        lo = t - REG_WIN_S
        for ev in self._ev:
            while ev and ev[0][0] < lo:
                del ev[0]
        hold = min(3.5, max(KICK_HOLD_S, 1.6 * self._iki))
        cur = self._driver
        if t - self._last[KICK] < hold:
            nk = len([e for e in self._ev[KICK] if t - e[0] < KICK_MODE_S])
            if nk >= 3 or (cur == KICK and nk >= 2):
                self._driver, self._dscore, self._dlost = KICK, 1.0, 0.0
                return
        if cur == KICK:
            cur = None
        score = {}
        if self._pause < PAUSE_MAX:  # fala: ninguem conduz
            for i, ev in ((BASSN, self._low_events()), (SNARE, self._ev[SNARE]), (BROAD, self._ev[BROAD])):
                nmin = REG_MIN_N
                if (len(ev) >= REG_SLOW_N and t - self._open_t >= PAUSE_S + 2.0
                        and float(np.median(np.diff([e[0] for e in ev]))) >= REG_SLOW_S):
                    nmin = REG_SLOW_N  # groove lento (um golpe por tempo a 60-100 bpm), so depois do aquecimento da pausa
                if len(ev) >= nmin and len(ev) - 1 <= REG_MAX_RATE * (ev[-1][0] - ev[0][0]):
                    sal = float(np.median([e[2] for e in ev]))
                    reg = _regularity(ev) if sal >= SAL_MIN else 0.0
                    if reg >= REG_MIN:  # o mais forte (energia que cada golpe traz) entre os que se repetem
                        score[i] = (0.4 + reg) * sal
        best = max(score, key=score.get) if score else None
        if cur is not None and cur not in score:
            self._dlost += DRIVE_EVERY_S
            if self._dlost < 0.5:  # uma falha passageira nao derruba quem conduz
                return
        if best is None:
            self._driver, self._dscore = None, 0.0
        elif cur is None or cur not in score or score[best] > SWITCH_RATIO * score[cur]:
            self._driver, self._dscore, self._dlost = best, min(1.0, 2.0 * score[best]), 0.0
        else:
            self._dscore, self._dlost = min(1.0, 2.0 * score[cur]), 0.0

    def _low_events(self):
        ev = self._ev[KICK] + self._ev[BASSN]
        ev.sort()
        return ev

    def _publish(self, pk):
        wall = self.clock()
        hit, sub, onset = self._hit, self._sub, self._onset
        old = self._out
        if pk is None:
            pk = [old.loud, old.bass, old.mid, old.treble, old.punch, old.bands]
        drv = self._driver
        # periodo e ultimo golpe de quem conduz: so mudam quando entra (ou sai) um golpe; a cada bloco so o
        # proximo golpe esperado anda
        key = (drv, tuple(len(e) for e in self._ev), tuple(e[-1][0] if e else 0.0 for e in self._ev))
        if key != self._pkey:
            ev = () if drv is None else self._low_events() if drv == BASSN else self._ev[drv]
            per, last = 0.0, None
            if len(ev) >= 4:
                ioi = np.diff([e[0] for e in ev[-9:]])
                per = float(np.median(ioi))
                # golpes regulares: quando vem o proximo (so para a lampada ficar livre antes; nada dispara nisso)
                if per > 0.2 and np.mean(np.abs(ioi - per) < 0.12 * per) >= 0.6:
                    last = ev[-1][0]
            self._pkey, self._pcache = key, (per, last)
        per, last = self._pcache
        nxt = None
        if last is not None:
            k = max(1, math.ceil((self._t - last) / per - 0.15))
            nxt = wall + (last + k * per - self._t)
        o = Features(wall=wall, active=self._act, gate=self._gate, loud=self._env[0], bass=self._env[1],
                     mid=self._env[2], treble=self._env[3], punch=self._punch, balance=self._bal, bands=self._bars,
                     beat=hit is not None, beats=int(hit is not None), beat_strength=hit[1] if hit else 0.0,
                     beat_wall=wall if hit else old.beat_wall, layer=LAYERS[hit[0]] if hit else "",
                     sub=sub is not None, sub_strength=sub[1] if sub else 0.0, sub_layer=LAYERS[sub[0]] if sub else "",
                     onset=onset > 0, onset_strength=onset, driver=LAYERS[drv] if drv is not None else "",
                     tempo_bpm=60.0 / per if per > 0.2 else 0.0, period=per, clarity=self._clar,
                     locked=drv is not None, next_beat=nxt, note=self._note, note_conf=self._nconf,
                     note_n=self._note_n)
        with self._lock:
            self._out = o
            a = self._acc
            if a is None:
                self._acc = o.copy(loud=pk[0], bass=pk[1], mid=pk[2], treble=pk[3], punch=pk[4], bands=pk[5])
            else:  # entre dois take(): picos dos envelopes e qualquer golpe
                keep = a.beat and (not hit or a.beat_strength >= hit[1])
                ksub = a.sub and (not sub or a.sub_strength >= sub[1])
                self._acc = o.copy(loud=max(a.loud, pk[0]), bass=max(a.bass, pk[1]), mid=max(a.mid, pk[2]),
                                   treble=max(a.treble, pk[3]), punch=max(a.punch, pk[4]),
                                   bands=np.maximum(a.bands, pk[5]), beat=a.beat or o.beat, beats=a.beats + o.beats,
                                   beat_strength=a.beat_strength if keep else o.beat_strength,
                                   beat_wall=o.beat_wall, layer=a.layer if keep else o.layer,
                                   sub=a.sub or o.sub, sub_strength=a.sub_strength if ksub else o.sub_strength,
                                   sub_layer=a.sub_layer if ksub else o.sub_layer, onset=a.onset or o.onset,
                                   onset_strength=max(a.onset_strength, o.onset_strength))
        if hit:
            self.beat_event.set()

    def take(self):
        """Features com picos e golpes acumulados desde o take() anterior: golpe entre ticks nao se perde."""
        with self._lock:
            a, self._acc = self._acc, None
            return a if a is not None else self._out.copy(beat=False, beats=0, beat_strength=0.0, sub=False,
                                                           sub_strength=0.0, onset=False, onset_strength=0.0)


class LightMapper:
    """Features -> (h, s, v) por lampada, a cada tick (10-20 Hz).
    O golpe de quem conduz sai num quadro de golpe (last_kind "beat"); ~80 ms depois um quadro rapido ("quick")
    derruba o flash; com alguem conduzindo nada mais muda entre os golpes (so a cor de uma nota nova), e antes
    do golpe esperado a lampada fica livre. Sem ninguem conduzindo (fala, ambiente) o brilho so respira.
    pulse: a cor do usuario (a nota do baixo so a desloca um pouco), flash no golpe com queda em fade;
    flow: a cor gira sempre em fade, mais rapido com golpes e notas novas do baixo; golpe so incha o brilho;
    party: a cor da nota do baixo (sem nota, cor nova a cada golpe), flash em degrau que clareia a cor.
    last_fire: o ultimo tick disparou; last_jump: salto (True) ou fade (False).
    latency fica so por compatibilidade: a luz reage ao golpe detectado, nunca a um previsto."""

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
        self._amp = [0.0] * self.n  # forca do ultimo flash de cada lampada
        self._nbeats = 0
        self._last_fire = -1e9
        self._last_change = -1e9
        self._fall = None           # quando sai a queda do flash
        self.wake = None
        self.last_fire = False
        self.last_kind = ""
        self.last_jump = True
        self._phase = 0.0
        self._push = 0.0
        self._bal = 0.0
        self._treb = 0.0
        self._yb = None             # brilho base (fora dos golpes), com subida limitada
        self._vs = [None] * self.n
        self._shown = None          # ultima saida (repetida enquanto a luz segura entre os golpes)
        self._party_h = self.base_hue
        self._note_n = 0
        self._note_t = -1e9         # quando a nota do baixo mudou pela ultima vez
        self._pulse_off = 0.0

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
        self._amp = (self._amp + [0.0] * n)[:n]
        self._vs = (self._vs + [None] * n)[:n]
        self._shown = None
        self.n = n

    def _beat_event(self, f, now, tick_dt):
        """(dispara, forca, previsto). So golpe detectado de quem conduz; previsto nunca."""
        if f.gate and f.beat and now - self._last_fire >= 0.1:
            return True, f.beat_strength, False
        return False, 0.0, False

    def tick(self, f, now=None):
        now = f.wall if now is None else now
        dt = 1 / 15 if self._now is None else max(1e-3, min(0.5, now - self._now))
        self._now = now
        sp, a, style = self.sens, f.active, self.style
        clar = min(1.0, max(0.0, f.clarity))  # 0 fala/ambiente .. 1 ritmo claro
        driven = bool(f.gate and f.driver)
        fire, strength, _pred = self._beat_event(f, now, dt)
        self.last_fire = fire
        if fire:
            self._last_fire = now
            self._nbeats += 1
            self._fall = now + HOLD_S
            amp = sp.flash * (0.55 + 0.45 * strength)
            for i in range(self.n):
                # varias lampadas: a cada golpe metade alternada leva o flash inteiro
                self._amp[i] = amp if (self.n == 1 or (i + self._nbeats) % 2 == 0) else 0.5 * amp
        # nota do baixo: muda a cor na hora (party, flow) ou desloca um pouco (pulse)
        if f.note_n != self._note_n:
            self._note_n = f.note_n
            self._note_t = now
        note = f.note if (f.note >= 0 and f.note_conf >= NOTE_ON) else -1
        since = now - self._last_fire
        if since < HOLD_S - 0.002:
            fx = 1.0
        else:
            fx = FALL_KEEP[style] * math.exp(-(since - HOLD_S) / FLASH_DECAY_S)
        fl = [x * fx for x in self._amp]
        depth = sp.depth
        rng = self.vmax - FLOOR_V
        v_rest = FLOOR_V + rng * self.rest
        top = DRIVEN_TOP if driven else BREATH_TOP

        if style == "pulse":
            drive = top * (0.6 * f.loud + 0.4 * f.punch)
            off = 0.0 if note < 0 else PULSE_NOTE_DEG * (note - 5.5) / 5.5
            self._pulse_off += (off - self._pulse_off) * (1.0 if note >= 0 else 1 - math.exp(-dt / 2.0))
            hs = [((self.base_hue + self._pulse_off) % 360, self.base_sat)] * self.n
        elif style == "flow":
            # a cor gira sempre para frente, em fade. Cada golpe de quem conduz e cada nota nova do baixo
            # empurram: musica rapida e forte gira mais rapido, calma ou fala quase para
            self._phase += dt * (FLOW_IDLE_DPS + FLOW_LOUD_DPS * f.loud * clar) * a
            if fire:
                self._push += FLOW_BEAT_DEG * (0.5 + 0.5 * strength)
            if note >= 0 and self._note_t == now:
                self._push += FLOW_NOTE_DEG
            self._push = min(self._push, FLOW_PUSH_MAX)
            step = min(self._push * (1 - math.exp(-dt / FLOW_PUSH_S)), FLOW_GLIDE * dt)
            self._push -= step
            self._phase = (self._phase + step) % 360
            self._bal += (f.balance - self._bal) * (1 - math.exp(-dt / 0.6))
            self._treb += (f.treble - self._treb) * (1 - math.exp(-dt / 0.4))
            # a matiz pende um pouco com o equilibrio grave x agudo (suavizado em 0.6 s)
            hue = self.base_hue + self._phase + 40.0 * self._bal
            sat = self.base_sat * (1.0 - 0.2 * self._treb * a)          # chimbal e ar: um pouco mais palido
            spread = 0.0 if self.n == 1 else min(30.0, 120.0 / (self.n - 1))
            hs = [((hue + (i - (self.n - 1) / 2) * spread) % 360, sat) for i in range(self.n)]
            drive = top * (0.6 * f.loud + 0.4 * f.punch)
            fl = [FLOW_FLASH * x for x in fl]
        else:  # party
            if note >= 0 and now - self._note_t < NOTE_STALE_S:
                self._party_h = (self.base_hue + note * NOTE_HUE) % 360
                self._last_change = now
            elif fire and now - self._last_change >= PARTY_GAP_S:
                self._party_h = (self._party_h + GOLDEN + self._rng.uniform(-20, 20)) % 360
                self._last_change = now
            elif f.gate and not f.driver and now - self._last_change > 4.0:
                self._party_h = (self._party_h + 8.0 * dt * a) % 360   # nada ritmico: deriva lenta
            step = 360.0 / self.n if self.n <= 4 else GOLDEN
            hs = [((self._party_h + i * step) % 360, 100.0 - PARTY_WHITE * min(1.0, fl[i])) for i in range(self.n)]
            drive = top * (0.25 + 0.5 * f.loud + 0.25 * f.punch)

        # fora dos golpes o brilho respira: sobe no maximo 100 %/s (nota do baixo nao vira flash), desce suave
        drive = 0.0 if drive < 0 else 1.0 if drive > 1 else drive
        yb = self._yb
        yb = drive if yb is None else min(drive, yb + BREATH_RISE * dt) if drive > yb \
            else drive + (yb - drive) * math.exp(-dt / 0.25)
        self._yb = yb
        # fala/ambiente: descida mais lenta, silabas nao piscam
        fall = math.exp(-dt / (0.08 + 0.3 * (1 - clar)))
        out = []
        for i in range(self.n):
            y = 1 - (1 - yb) * (1 - fl[i])  # screen: o flash puxa para o maximo
            y = 0.0 if y < 0 else 1.0 if y > 1 else y
            v = FLOOR_V + rng * ((1 - depth) + depth * y ** GAMMA)
            ai = max(a, min(1.0, fl[i]))  # o flash nao espera o "active" subir: o primeiro bumbo ja pisca
            v = ai * v + (1 - ai) * v_rest
            vs = self._vs[i]
            # no flash e na queda dele a luz segue na hora; fora disso desce devagar
            vs = v if vs is None or v > vs or since < 1.0 else v + (vs - v) * fall
            self._vs[i] = vs
            h, s = hs[i]
            out.append((int(round(h)) % 360, int(round(s)), max(FLOOR_V, min(100, int(round(vs))))))

        # que quadro e este: golpe, queda do flash / cor de nota nova (rapido) ou comum
        kind, jump = "", style != "flow"
        shown = self._shown
        if fire:
            kind, jump = "beat", style != "flow"
        elif self._fall is not None and now >= self._fall:
            self._fall = None
            kind, jump = "quick", FALL_JUMP[style]
        elif shown is not None and len(shown) == len(out):
            hue_moved = any(_hue_d(o[0], p[0]) >= 3 for o, p in zip(out, shown))
            nb = f.next_beat
            idle = nb is not None and nb - IDLE_S <= now < nb + 0.03
            held = driven and style != "flow" and now - self._last_fire < 2.0
            if hue_moved and now - self._note_t < 0.5 and not idle and not held and style != "flow":
                kind, jump = "quick", style == "party"  # sem golpes regulares a nota nova vai logo (flow desliza)
            elif idle or held:
                # com alguem conduzindo a luz segura entre os golpes (so muda no golpe, na queda e na nota)
                out = [(o[0], o[1], p[2]) for o, p in zip(out, shown)] if hue_moved and not idle else shown
            if style != "party" and kind == "":
                jump = False
        self._shown = out
        self.last_kind, self.last_jump = kind, jump
        self.wake = self._fall      # o tick acorda na hora da queda (o tick comum e de 67 ms)
        return out


def _hue_d(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


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
        self._requick = set()          # lampadas cujo quadro rapido foi recusado: o reenvio tambem e rapido
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
            self._requick = set()
        self._cap_state, self._cap_detail = None, ""
        self._last_block = 0.0
        self._last_sound = CLOCK()
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
            self._requick = set()
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
                self._requick.discard(lamp)
            self._mapper.resize(len(self._lamps))

    # ----- thread da captura -----
    def _block(self, gen, an, x):
        if gen != self._gen:
            return
        now = CLOCK()
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
            wake = self._mapper.wake if self._mapper else None
            wait = TICK_S if wake is None else min(TICK_S, max(0.002, wake - CLOCK()))
            an.beat_event.wait(wait)
            an.beat_event.clear()
            if halt.is_set():
                break
            now = CLOCK()
            f = an.take()
            if now - self._last_block > STALE_S:  # captura parada: repouso calmo, sem congelar no ultimo quadro
                f = Features(wall=now)
            with self._lock:
                # lampada que saiu sozinha da musica (app do celular, desligada, conexao caiu) fica de fora
                self._drop([lamp for lamp in self._lamps if not _call(lambda: lamp.music_active)])
                out = self._mapper.tick(f, now)
                kind = self._mapper.last_kind  # golpe e quick: a lampada manda antes dos outros
                beat = kind == "beat"
                jump = self._mapper.last_jump
                sends = []
                for lamp, c in zip(self._lamps, out):
                    if needs_send(self._sent.get(lamp), c):
                        self._sent[lamp] = c
                        sends.append((lamp, c))
            for lamp, (h, s, v) in sends:
                # quadro rapido recusado (o golpe ainda estava na fila) volta como rapido no proximo tick: como
                # quadro comum a queda do flash so sairia no caminho lento (~250 ms no pico em vez de ~90 ms)
                q = {"quick": True} if (kind == "quick" or lamp in self._requick) and not beat else {}
                # nao bloqueia: a lampada guarda so o ultimo. False: um golpe ainda esperava, este vai no proximo tick
                if _call(lamp.music_frame, h, s, v, jump=jump, beat=beat, **q) is False:
                    with self._lock:
                        self._sent.pop(lamp, None)
                        if q:
                            self._requick.add(lamp)
                else:
                    with self._lock:
                        self._requick.discard(lamp)
            if DEBUG and beat:  # _call: print com stdout fechado nao derruba a thread do tick
                _call(print, f"lume-music golpe {f.layer} forca {f.beat_strength:.2f} conduz {f.driver} nota {f.note} | "
                      f"detectado {f.beat_wall:.3f} tick +{1000 * (now - f.beat_wall):.0f} ms | {len(sends)} quadro(s)",
                      flush=True)
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
