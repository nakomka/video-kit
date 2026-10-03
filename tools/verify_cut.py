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
print("ПОВТОРЫ: " + ("не найдены" if not bad else f"{bad} подозрительных места — разберись до графики"))
