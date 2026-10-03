"""Проверка реза: каждый интервал EDL из смонтированной дорожки распознаётся
отдельно (с запасом по 0.25 с), а длинные разбиваются на куски ≤ 5 с по стыкам.
Плюс замер остатка тишины.

python tools/verify_cut.py .tmp/cut16k.wav
"""
import sys

import numpy as np

from common import load_json
from envelope import envelope
from fragcheck import transcribe_piece
from speechmask import smooth_max, runs, HOP

wav = sys.argv[1]
segs = load_json(".tmp/segments.json")
t, bounds = 0.0, []
for s in segs:
    d = s["b"] - s["a"]
    bounds.append((s["span"], t, t + d))
    t += d
total = t

# группируем сегменты в куски ≤ 5 с (граница — только стык сегментов)
pieces, cur = [], None
for span, a, b in bounds:
    if cur and b - cur[1] <= 5.0:
        cur = (cur[0] + "|" + span if span not in cur[0] else cur[0], cur[1], b)
    else:
        if cur:
            pieces.append(cur)
        cur = (span, a, b)
pieces.append(cur)
heard = []
for span, a, b in pieces:
    txt = transcribe_piece(wav, max(0, a - 0.25), min(total, b + 0.25), "vc")
    print(f"  [{a:6.2f}-{b:6.2f}] {span:10s} {txt}")
    heard.append((a, b, txt))

# остаток тишины: самые длинные участки ниже порога на смонтированной дорожке
db = envelope(wav)
g = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
quiet = smooth_max(db + g) < -30
gaps = sorted(((b - a) * HOP, a * HOP) for a, b in runs(quiet) if a > 0 and b < len(quiet))
print(f"длительность {total:.3f} с; самые длинные паузы (с усилением {g} дБ):")
for d, at in gaps[-6:][::-1]:
    print(f"  {d*1000:5.0f} мс @ {at:.2f}")

# ПОВТОРЫ И ФАЛЬСТАРТЫ: соседние куски начинаются одинаково («Отдаю абсолют…» → «Отдаю абсолютно…»),
# либо кусок распознаётся абракадаброй, похожей на начало следующего. Whisper по целому файлу их
# склеивает в одну фразу — поэтому сравниваем куски по буквам.
import difflib
import re as _re


def _letters(t, n=14):
    return _re.sub(r"[^а-яёa-z]", "", t.lower())[:n]


bad = 0
for (a1, b1, t1), (a2, b2, t2) in zip(heard, heard[1:]):
    x, y = _letters(t1), _letters(t2)
    if len(x) >= 6 and len(y) >= 6 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.45:
        bad += 1
        print(f"  ✗ ПОВТОР/ФАЛЬСТАРТ? [{a1:.2f}-{b1:.2f}] «{t1[:40]}» ≈ [{a2:.2f}-{b2:.2f}] «{t2[:40]}» — "
              "послушай и выкинь неудачный заход")
for a1, b1, t1 in heard:
    words = _re.findall(r"[а-яёa-z]+", t1.lower())
    if len(words) >= 4 and len(set(words)) <= len(words) / 2:
        bad += 1
        print(f"  ✗ кусок [{a1:.2f}-{b1:.2f}] распознан с повтором «{t1[:40]}» — вероятен неудачный дубль")
# одна и та же фраза из 3+ слов дважды где угодно в резе (кроме стыка соседних кусков: они
# распознаются с запасом 0.25 с и честно пересекаются на 1–2 словах)
pos = {}
for pi, (a1, b1, t1) in enumerate(heard):
    w = _re.findall(r"[а-яёa-z0-9]+", t1.lower().replace("ё", "е"))
    for wi in range(len(w) - 2):
        pos.setdefault(tuple(w[wi:wi + 3]), []).append((pi, wi, len(w), a1))
    for x, y in zip(w, w[1:]):
        if x == y and len(x) > 1:
            bad += 1
            print(f"  ✗ ЗАПИНКА в [{a1:.2f}-{b1:.2f}]: «{x} {y}»")
for g, occ in pos.items():
    for (p1, w1, n1, t1), (p2, w2, n2, t2) in zip(occ, occ[1:]):
        edge = p2 == p1 + 1 and w1 >= n1 - 5 and w2 <= 2
        if not edge:
            bad += 1
            print(f"  ✗ ФРАЗА ДВАЖДЫ: «{' '.join(g)}» около {t1:.1f} с и {t2:.1f} с")
print("ПОВТОРЫ: " + ("не найдены" if not bad else f"{bad} подозрительных места — разберись до графики"))

# щелчки на стыках: скачок сигнала на границе сегмента против обычных соседних отсчётов
import os as _os
import subprocess as _sp
import numpy as _np
if _os.path.exists(".tmp/cut48k.wav"):
    _x = _np.frombuffer(_sp.run(["ffmpeg", "-v", "error", "-i", ".tmp/cut48k.wav", "-ac", "1", "-f", "f32le", "-"],
                                capture_output=True).stdout, dtype=_np.float32)
    _d = _np.abs(_np.diff(_x))
    clicks, tt = 0, 0.0
    for s_ in segs[:-1]:
        tt += s_["b"] - s_["a"]
        i = int(tt * 48000)
        near = _d[max(0, i - 96):i + 96]
        ref = _np.median(_d[max(0, i - 4800):i + 4800]) + 1e-6
        if len(near) and near.max() / ref > 40 and near.max() > 0.02:
            clicks += 1
            print(f"  ✗ ЩЕЛЧОК на стыке {tt:.2f} с (скачок ×{near.max() / ref:.0f})")
    print("ЩЕЛЧКИ: " + ("нет" if not clicks else f"{clicks} — удлини фейд на стыке"))
