"""Проверка звука перед выдачей. Запускай после audio_master.py — ответ должен быть «QA ЗВУК: OK».

python tools/qa_audio.py

Ловит то, что уже случалось в роликах:
  - клиппинг микса (пик выше −0.5 dBFS);
  - «музыка умерла»: подложка падает больше чем на 16 дБ за 1.5 с (нормальное приглушение под голос —
    12–15 дБ с рампой; провал 20 дБ за секунду — ошибка);
  - шум: пол шума голоса в паузах между словами выше −50 dBFS (шипение тихой записи после подъёма);
  - разнос голос/подложка в речи вне 10–20 дБ;
  - «поломки» музыки: наша обработка меняет громкость подложки больше чем на 6 дБ за 60 мс
    (пампинг сайдчейна, дыры от просадок) — сравнение bed_ducked с исходной bed_norm0;
  - длительность микса ≠ длительности голоса;
  - шипение, добавленное обработкой: доля 12–20 кГц в голосе выше, чем в резе (.tmp/cut48k.wav),
    больше чем на 6 дБ (так звучала склейка anullsrc + concat — «пшшш» под всей речью).
"""
import subprocess
import sys

import numpy as np

from common import ROOT

HOP = 0.05


def load(path):
    # моно — СРЕДНЕЕ каналов (ffmpeg -ac 1 складывает с +3 дБ и даёт ложный клиппинг)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-af",
                          "aformat=channel_layouts=stereo,pan=mono|c0=0.5*c0+0.5*c1", "-ar", "16000", "-f", "f32le", "-"],
                         cwd=ROOT, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def env(x):
    n = int(16000 * HOP)
    k = len(x) // n
    return 20 * np.log10(np.maximum(np.sqrt((x[: k * n].reshape(k, n) ** 2).mean(1)), 1e-9))


voice, bed, mix = load(".tmp/voice.wav"), load(".tmp/bed_ducked.wav"), load(".tmp/audio_mixed.wav")
ev, eb = env(voice), env(bed)
m = min(len(ev), len(eb))
ev, eb = ev[:m], eb[:m]
fails, notes = [], []

# пик — по исходному файлу (истинный пик, 4× передискретизация в ebur128), не по 16 кГц моно
r = subprocess.run(["ffmpeg", "-i", ".tmp/audio_mixed.wav", "-af", "ebur128=peak=true", "-f", "null", "-"],
                   cwd=ROOT, capture_output=True, text=True).stderr
import re
peak = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r)[-1])
if peak > -0.5:  # цель TP −1
    fails.append(f"клиппинг: пик микса {peak:.1f} dBFS")

# подложка: сглаживание 1 с, сравнение «до» и «через 1.5 с»
w = int(1.0 / HOP)
sm = np.convolve(eb, np.ones(w) / w, mode="same")
d = int(1.5 / HOP)
for i in range(w, m - d - w):
    if sm[i] - sm[i + d] > 16:
        fails.append(f"музыка проваливается на {sm[i] - sm[i + d]:.0f} дБ около {i * HOP:.1f}–{(i + d) * HOP:.1f} с "
                     "(нужна плавная рампа, см. --bed-free)")
        break

# шум в паузах между словами: окна тише речи на 25+ дБ внутри речевых участков
speech = ev > -35
idx = np.where(speech)[0]
if len(idx):
    inside = np.zeros(m, bool)
    inside[idx[0]:idx[-1]] = True
    gaps = ev[inside & (ev < np.median(ev[speech]) - 25) & (ev > -100)]
    if len(gaps) >= 4:
        floor = float(np.percentile(gaps, 30))
        (fails if floor > -50 else notes).append(f"пол шума голоса в паузах {floor:.1f} dBFS"
                                                + (" — нужен шумодав" if floor > -50 else ""))

# рывки громкости, внесённые обработкой (не сам трек): усиление = ducked − norm0, окна 20 мс
def env20(x):
    k = len(x) // 320
    return 20 * np.log10(np.maximum(np.sqrt((x[: k * 320].reshape(k, 320) ** 2).mean(1)), 1e-9))
b0, b1 = env20(load(".tmp/bed_norm0.wav")), env20(bed)
k = min(len(b0), len(b1))
gain = np.convolve(b1[:k] - b0[:k], np.ones(3) / 3, mode="same")
ok = b0[:k] > -45
jumps = [i for i in range(3, k - 3) if ok[i] and ok[i + 3] and abs(gain[i + 3] - gain[i]) > 6]
if jumps:
    fails.append(f"рывки громкости музыки ({len(jumps)} шт., первый {jumps[0] * 0.02:.2f} с) — пампинг/дыры, "
                 "нужно плавное приглушение (--duck smooth)")

sep = np.median(ev[speech] - eb[speech]) if speech.any() else 0
(notes if 10 <= sep <= 20 else fails).append(f"разнос голос/подложка в речи {sep:.1f} дБ")

# спектр голоса против реза: обработка не должна добавлять верх
def hf_share(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-af",
                          "aformat=channel_layouts=stereo,pan=mono|c0=0.5*c0+0.5*c1", "-ar", "48000", "-f", "f32le", "-"],
                         cwd=ROOT, capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    n = 4800
    k = len(x) // n
    fr = x[: k * n].reshape(k, n)
    fr = fr[np.sqrt((fr ** 2).mean(1)) > 10 ** (-40 / 20) * (np.abs(x).max() + 1e-9)]  # только речь
    X = (np.abs(np.fft.rfft(fr * np.hanning(n), axis=1)) ** 2).sum(0)
    f = np.fft.rfftfreq(n, 1 / 48000)
    return 10 * np.log10(X[(f >= 12000) & (f < 20000)].sum() / X.sum())


import os
if os.path.exists(os.path.join(ROOT, ".tmp", "cut48k.wav")):
    h_cut, h_voice = hf_share(".tmp/cut48k.wav"), hf_share(".tmp/voice.wav")
    if h_voice - h_cut > 6:
        fails.append(f"обработка добавила шипение: доля 12–20 кГц {h_voice:.1f} дБ против {h_cut:.1f} в резе "
                     "(дорожку голоса собирай tools/voice_track.py)")
    else:
        notes.append(f"верх голоса 12–20 кГц: {h_voice:.1f} дБ (рез {h_cut:.1f}) — шипения не добавлено")

dv, dm = len(voice) / 16000, len(mix) / 16000
if abs(dv - dm) > 0.05:
    fails.append(f"длительность микса {dm:.2f} с ≠ голоса {dv:.2f} с")

for n in notes:
    print("  " + n)
for f in fails:
    print("  ✗ " + f)
print("QA ЗВУК: " + ("OK" if not fails else "ОШИБКИ — исправь до выдачи"))
sys.exit(1 if fails else 0)
