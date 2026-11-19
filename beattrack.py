"""Deteccao de batida online (causal): o modelo "online" de beat tracking do madmom (Boeck, Krebs e Widmer,
ISMIR 2014-2016), portado para numpy puro. O madmom nao tem mais versao que instale no Python novo nem pacote
para Windows; isto e so o caminho online dele, com os mesmos numeros (ver assets/beats_online.LICENSE).

Cadeia: audio 44.1 kHz -> quadro de 2048 amostras a cada 441 (100/s), Hann, |FFT| -> 81 bandas logaritmicas
(12 por oitava, 30 Hz-17 kHz) -> log10(1 + x) -> + diferenca positiva com o quadro anterior (162 valores) ->
media de 8 LSTMs (3 camadas de 25, com peephole) -> ativacao 0-1 "isto e batida" -> HMM de andamento
(55-215 bpm, forward a cada quadro): batida quando o estado mais provavel cai no comeco do tempo."""
import os

import numpy as np

SR = 44100
FRAME = 2048
HOP = 441               # 100 quadros por segundo
FPS = SR / HOP
MIN_BPM, MAX_BPM = 55.0, 215.0
TRANSITION_LAMBDA = 100.0
OBSERVATION_LAMBDA = 16
UNITS = 25

_HERE = getattr(__import__("sys"), "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(_HERE, "assets", "beats_online.npz")


def _sigmoid(x):
    return 0.5 * np.tanh(0.5 * x) + 0.5  # = 1 / (1 + e^-x), sem overflow


class BeatActivation:
    """Audio (44.1 kHz, float -1..1) -> ativacao de batida por quadro. Estado entre chamadas (streaming)."""

    GATES = ("input_gate", "forget_gate", "cell", "output_gate")

    def __init__(self, path=MODEL_PATH):
        z = np.load(path)
        self.fb = z["filterbank"].astype(np.float32)                 # (1024, 81)
        self.diff_frames = int(z["diff_frames"])
        n = len([k for k in z.files if k.endswith("_out_b")])
        self.n = n
        # as 4 portas lado a lado (i, f, c, o) e os 8 modelos empilhados: uma conta por camada
        self.W, self.R, self.B, self.P = [], [], [], []
        for li in range(3):
            w = np.stack([np.concatenate([z[f"m{m}_l{li}_{g}_w"] for g in self.GATES], axis=1) for m in range(n)])
            r = np.stack([np.concatenate([z[f"m{m}_l{li}_{g}_r"] for g in self.GATES], axis=1) for m in range(n)])
            b = np.stack([np.concatenate([z[f"m{m}_l{li}_{g}_b"] for g in self.GATES]) for m in range(n)])
            p = np.stack([np.stack([z[f"m{m}_l{li}_{g}_p"] for g in ("input_gate", "forget_gate", "output_gate")])
                          for m in range(n)])
            if li == 0:  # a entrada e a mesma para os 8: uma matriz (162, 8*100)
                w = np.concatenate(list(w), axis=1)
            self.W.append(w.astype(np.float32))
            self.R.append(r.astype(np.float32))
            self.B.append(b.astype(np.float32))
            self.P.append(p.astype(np.float32))
        self.Wo = np.stack([z[f"m{m}_out_w"][:, 0] for m in range(n)]).astype(np.float32)  # (8, 25)
        self.Bo = np.array([z[f"m{m}_out_b"][0] for m in range(n)], np.float32)
        # o madmom le o WAV inteiro (int16) e divide a janela por 32767; aqui o audio ja vem -1..1 (/32768)
        self.win = (np.hanning(FRAME) * (32768.0 / 32767.0)).astype(np.float32)
        self.reset()

    def reset(self):
        self.h = [np.zeros((self.n, UNITS), np.float32) for _ in range(3)]
        self.c = [np.zeros((self.n, UNITS), np.float32) for _ in range(3)]
        self.prev = []  # espectros dos ultimos quadros (diferenca)

    def features(self, frame):
        """Um quadro de 2048 amostras -> 162 valores (espectro log + diferenca positiva)."""
        spec = np.abs(np.fft.rfft(frame * self.win)[:FRAME // 2]).astype(np.float32)
        spec = np.log10(spec @ self.fb + 1.0)
        prev = self.prev
        diff = np.maximum(spec - prev[0], 0.0) if len(prev) >= self.diff_frames else np.zeros_like(spec)
        prev.append(spec)
        if len(prev) > self.diff_frames:
            del prev[0]
        return np.concatenate((spec, diff))

    def step(self, x):
        """162 valores -> ativacao (media dos 8 modelos)."""
        n, u = self.n, UNITS
        for li in range(3):
            if li == 0:
                z = (x @ self.W[0]).reshape(n, 4 * u)
            else:
                z = np.einsum("mi,mij->mj", self.h[li - 1], self.W[li])
            z += self.B[li] + np.einsum("mi,mij->mj", self.h[li], self.R[li])
            c0, p = self.c[li], self.P[li]
            ig = _sigmoid(z[:, :u] + c0 * p[:, 0])
            fg = _sigmoid(z[:, u:2 * u] + c0 * p[:, 1])
            c = np.tanh(z[:, 2 * u:3 * u]) * ig + c0 * fg
            og = _sigmoid(z[:, 3 * u:] + c * p[:, 2])
            self.c[li] = c
            self.h[li] = np.tanh(c) * og
        return float(np.mean(_sigmoid(np.einsum("mi,mi->m", self.h[2], self.Wo) + self.Bo)))


class BeatHMM:
    """HMM de andamento do madmom (DBNBeatTrackingProcessor, modo online): forward a cada quadro."""

    def __init__(self, fps=FPS):
        self.fps = fps
        iv = np.arange(np.round(60.0 * fps / MAX_BPM), np.round(60.0 * fps / MIN_BPM) + 1).astype(int)
        self.intervals = iv
        self.first = np.cumsum(np.r_[0, iv[:-1]]).astype(int)
        self.last = np.cumsum(iv) - 1
        ns = int(iv.sum())
        self.n = ns
        self.pos = np.concatenate([np.linspace(0, 1, i, endpoint=False) for i in iv])
        self.sint = np.repeat(iv, iv)
        self.beat_state = self.pos < 1.0 / OBSERVATION_LAMBDA
        # troca de andamento so na virada do tempo, mais provavel para andamento parecido
        ratio = iv[None, :].astype(float) / iv[:, None].astype(float)
        prob = np.exp(-TRANSITION_LAMBDA * np.abs(ratio - 1.0))
        prob[prob <= np.spacing(1)] = 0
        self.trans = prob / prob.sum(axis=1, keepdims=True)  # (de, para)
        self.inner = np.setdiff1d(np.arange(ns), self.first)
        self.reset()

    def reset(self):
        self.fwd = np.full(self.n, 1.0 / self.n)
        self.counter = 0
        self.last_beat = 0.0
        self.state = 0

    def step(self, act):
        """Ativacao de um quadro -> (batida neste quadro?, estado mais provavel)."""
        prev = self.fwd
        cur = np.empty_like(prev)
        cur[self.inner] = prev[self.inner - 1]
        cur[self.first] = prev[self.last] @ self.trans
        cur *= np.where(self.beat_state, act, (1.0 - act) / (OBSERVATION_LAMBDA - 1))
        s = cur.sum()
        self.fwd = cur / s if s > 0 else np.full(self.n, 1.0 / self.n)
        st = int(np.argmax(self.fwd))
        self.state = st
        t = self.counter / self.fps
        self.counter += 1
        # o forward costuma marcar varios quadros seguidos: so vale batida a mais de 60/215 s da anterior
        if self.beat_state[st] and t >= self.last_beat + 60.0 / MAX_BPM:
            self.last_beat = t
            return True, st
        return False, st

    def bpm(self):
        return 60.0 * self.fps / self.sint[self.state]

    def phase(self):
        return float(self.pos[self.state])


class OnlineBeatTracker:
    """feed(blocos de audio 44.1 kHz) -> lista de (tempo_no_stream_s, pico da ativacao) das batidas.
    Cada quadro usa as ultimas 2048 amostras (causal): a batida sai ~23 ms depois do centro do quadro."""

    def __init__(self, path=MODEL_PATH):
        self.act = BeatActivation(path)
        self.hmm = BeatHMM()
        self.reset()

    def reset(self):
        self.act.reset()
        self.hmm.reset()
        self._buf = np.zeros(FRAME, np.float32)
        self._fill = 0          # amostras novas desde o ultimo quadro
        self._n = 0             # amostras recebidas
        self.activation = 0.0
        self._recent = [0.0] * 7  # ativacoes dos ultimos 70 ms: o pico perto da batida e a forca dela

    def feed(self, x):
        x = np.asarray(x, np.float32)
        beats = []
        i = 0
        while i < len(x):
            take = min(HOP - self._fill, len(x) - i)
            self._buf = np.concatenate((self._buf[take:], x[i:i + take]))
            self._fill += take
            self._n += take
            i += take
            if self._fill == HOP:
                self._fill = 0
                a = self.act.step(self.act.features(self._buf))
                self.activation = a
                self._recent = self._recent[1:] + [a]
                beat, _st = self.hmm.step(a)
                if beat:
                    beats.append((self._n / SR, max(self._recent)))
        return beats

    @property
    def bpm(self):
        return self.hmm.bpm()

    @property
    def phase(self):
        return self.hmm.phase()
