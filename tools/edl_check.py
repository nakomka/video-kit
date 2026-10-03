"""Проверка EDL перед фиксацией и расчёт сегментов реза.

python tools/edl_check.py            # печать + .tmp/segments.json
"""
import os

from common import TMP, load_json, save_json, words_from_whisper
from speechmask import speech_mask, to_segments, pauses, runs, HOP

import sys

edl = load_json("edl_spans.json")
density = edl.get("density", "максимальная")
gains = edl.get("analysis_gain_db", {})
if "--gain" in sys.argv:
    g = float(sys.argv[sys.argv.index("--gain") + 1])
    gains = {k: g for k in "12345"}
all_segs, total = [], 0.0
cache = {}
for sp in edl["spans"]:
    sid = sp["src"]
    if sid not in cache:
        db = load_json(f".tmp/env{sid}.json")["db"]
        words = words_from_whisper(os.path.join(TMP, f"words{sid}.json"))
        rep = []
        g = float(gains.get(sid, 0.0))
        speech_mask(db, words, rep, gain=g)
        cache[sid] = (db, words, rep, g)
    db, words, rep, g = cache[sid]
    segs = to_segments(db, sp["in"], sp["out"], density, words=words, gain=g)
    long_p = pauses(db, sp["in"], sp["out"], 1.0, words=words, gain=g)
    # хвост чужого дубля: речь в начале, потом пауза > 0.7 с
    mask = speech_mask(db, words, gain=g)
    i0, i1 = int(sp["in"] / HOP), int(sp["out"] / HOP)
    r = runs(mask[i0:i1])
    tail_warn = ""
    if len(r) > 1 and (r[1][0] - r[0][1]) * HOP > 0.7 and (r[0][1] - r[0][0]) * HOP < 1.5:
        tail_warn = f"  ⚠ начало {r[0][0]*HOP+sp['in']:.2f}-{r[0][1]*HOP+sp['in']:.2f}, затем пауза"
    dur = sum(b - a for a, b in segs)
    total += dur
    resc = [x for x in rep if sp["in"] <= x[1] < sp["out"]]
    print(f"{sp['id']} src{sid} {sp['in']:.2f}-{sp['out']:.2f} -> {len(segs)} сегм, {dur:.2f} с"
          + (f"  ⚠ паузы>1с: {long_p}" if long_p else "") + tail_warn
          + (f"  спасено: {resc}" if resc else ""))
    for a, b in segs:
        all_segs.append({"span": sp["id"], "src": sid, "a": a, "b": b})
# соседние интервалы одного клипа: отступы −20/+30 мс могут перекрываться —
# без этого на стыке звучит один и тот же кадр дважды
merged = []
for s in all_segs:
    p = merged[-1] if merged else None
    if p and p["src"] == s["src"] and s["a"] <= p["b"] + 1e-6:
        p["b"] = max(p["b"], s["b"])
        if p["span"] != s["span"]:
            p["span"] = p["span"] + "+" + s["span"]
        continue
    merged.append(dict(s))
all_segs = merged
total = sum(s["b"] - s["a"] for s in all_segs)
save_json(".tmp/segments.json", all_segs)
print(f"ИТОГО: {len(all_segs)} сегментов, {total:.2f} с")

# неудачные заходы (tools/takes.py): интервал EDL не должен захватывать фальстарт или запинку
if os.path.exists(os.path.join(TMP, "takes.json")):
    bad = load_json(".tmp/takes.json")["bad"]
    hits = 0
    for sp in edl["spans"]:
        for sid, a, b, why in bad:
            ov = min(sp["out"], b) - max(sp["in"], a)
            if sid == sp["src"] and ov > 0.5 * (b - a):
                hits += 1
                print(f"  ✗ {sp['id']} {sp['in']:.2f}-{sp['out']:.2f} захватывает {a:.2f}–{b:.2f}: {why}")
    print("ДУБЛИ В EDL: " + ("чисто" if not hits else f"{hits} неудачных заходов — исправь EDL"))
else:
    print("ДУБЛИ В EDL: не проверено — сначала python tools/takes.py")
