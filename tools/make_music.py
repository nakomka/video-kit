"""Синтез подложки: игривый бит 120 BPM (маримба + пиццикато-бас + ударные).

Структура: такты 0–11 — лёгкое интро (маримба, шейкер, хлопки),
с такта 12 (24.0 с) — вход полной группы (бочка, малый, бас, октава в мелодии).
Время дропа пишется в .tmp/music_drop.txt — его читает audio_master.py.

python tools/make_music.py  ->  .tmp/music.wav
"""
import os
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 48000
BPM = 120
BEAT = 60 / BPM
BAR = 4 * BEAT
BARS = 18
DROP_BAR = 12
rng = np.random.default_rng(7)
N = int(SR * BARS * BAR) + SR
L = np.zeros(N)
R = np.zeros(N)


def add(sig, t, pan=0.0, gain=1.0):
    i = int(t * SR)
    j = min(N, i + len(sig))
    s = sig[: j - i] * gain
    L[i:j] += s * np.sqrt(0.5 * (1 - pan))
    R[i:j] += s * np.sqrt(0.5 * (1 + pan))


def env(n, dec):
    t = np.arange(n) / SR
    a = np.minimum(1, t / 0.003)
    return a * np.exp(-t / dec)


def marimba(f, dur=0.5):
    n = int(SR * dur)
    t = np.arange(n) / SR
    s = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * f * 3.93 * t) * np.exp(-t / 0.04)
    return s * env(n, 0.22)


def pluck_bass(f, dur=0.45):
    n = int(SR * dur)
    t = np.arange(n) / SR
    s = sum(np.sin(2 * np.pi * f * k * t) / k ** 1.6 for k in range(1, 6))
    return s * env(n, 0.16)


def kick():
    n = int(SR * 0.35)
    t = np.arange(n) / SR
    f = 45 + 90 * np.exp(-t / 0.04)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.12)


def noise(n):
    return rng.standard_normal(n)


def hp(x, a=0.85):
    y = np.zeros_like(x)
    for i in range(1, len(x)):
        y[i] = a * (y[i - 1] + x[i] - x[i - 1])
    return y


def snare():
    n = int(SR * 0.2)
    t = np.arange(n) / SR
    return (0.7 * hp(noise(n), 0.7) + 0.5 * np.sin(2 * np.pi * 190 * t)) * env(n, 0.07)


def hat(dec=0.025):
    n = int(SR * 0.08)
    return hp(noise(n), 0.95) * env(n, dec)


def clap():
    n = int(SR * 0.15)
    x = hp(noise(n), 0.8) * env(n, 0.05)
    out = np.zeros(n + 800)
    for k, d in enumerate((0, 250, 520)):
        out[d:d + n] += x * (0.6 if k < 2 else 1)
    return out


def hz(m):
    return 440 * 2 ** ((m - 69) / 12)


# C  Am  F  G  (по такту)
CHORDS = [(48, [60, 64, 67]), (45, [57, 60, 64]), (41, [57, 60, 65]), (43, [59, 62, 67])]
SCALE = [60, 62, 64, 67, 69, 72, 74, 76]
# мотив на 8 восьмых (-1 = пауза), варьируется по тактам
MOTIFS = [[0, -1, 2, 4, -1, 4, 3, -1], [5, -1, 4, 2, -1, 1, 2, -1],
          [3, 4, -1, 5, 4, -1, 2, -1], [1, -1, 2, -1, 4, 3, 1, -1]]

for bar in range(BARS):
    t0 = bar * BAR
    full = bar >= DROP_BAR
    root, chord = CHORDS[bar % 4]
    motif = MOTIFS[bar % 4]
    for k, step in enumerate(motif):
        if step < 0:
            continue
        m = SCALE[step]
        add(marimba(hz(m)), t0 + k * BEAT / 2, pan=-0.2, gain=0.30)
        if full:
            add(marimba(hz(m + 12), 0.3), t0 + k * BEAT / 2, pan=0.3, gain=0.12)
    # аккорд маримбой на 1 и 3
    for b in (0, 2):
        for m in chord:
            add(marimba(hz(m), 0.6), t0 + b * BEAT + 0.005, pan=0.25, gain=0.10)
    # шейкер восьмыми
    for k in range(8):
        add(hat(0.018 if k % 2 else 0.03), t0 + k * BEAT / 2, pan=0.4, gain=0.10 if full else 0.07)
    if not full:
        if bar >= 4:
            for b in (1, 3):
                add(clap(), t0 + b * BEAT, gain=0.12)
        if bar == DROP_BAR - 1:  # подводка: дробь малым на последней доле
            for k in range(8):
                add(snare(), t0 + 3 * BEAT + k * BEAT / 8, gain=0.08 + 0.03 * k)
        continue
    for b in range(4):
        add(kick(), t0 + b * BEAT, gain=0.9)
        add(pluck_bass(hz(root)), t0 + b * BEAT + (BEAT / 2 if b % 2 else 0), gain=0.45)
    add(pluck_bass(hz(root + 7)), t0 + 3.5 * BEAT, gain=0.35)
    for b in (1, 3):
        add(snare(), t0 + b * BEAT, gain=0.45)
        add(clap(), t0 + b * BEAT, gain=0.18)

x = np.stack([L, R], axis=1)
x /= np.max(np.abs(x)) * 1.12
os.makedirs(os.path.join(ROOT, ".tmp"), exist_ok=True)
with wave.open(os.path.join(ROOT, ".tmp", "music.wav"), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((x * 32767).astype(np.int16).tobytes())
open(os.path.join(ROOT, ".tmp", "music_drop.txt"), "w").write(f"{DROP_BAR * BAR:.3f}")
print(f".tmp/music.wav {len(x)/SR:.1f} s, drop at {DROP_BAR * BAR:.2f} s")
