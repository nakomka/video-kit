"""Профиль громкости музыкального трека и поиск дропа.

python tools/music_fit.py            # печать профиля (1 с) и кандидатов дропа
Дроп = место наибольшего роста кратковременной громкости (3 с после против 3 с до).
Смещение трека = время дропа − метка энд-карда (cues: endIn, другая — audio_master.py --drop-cue).
"""
import re
import subprocess

from common import ROOT, load_json

import os

# трек человека — первый .mp3/.wav/.m4a в папке проекта; нет трека — синтез tools/make_music.py
_tracks = sorted(f for f in os.listdir(ROOT) if os.path.splitext(f)[1].lower() in (".mp3", ".wav", ".m4a"))
MUSIC = _tracks[0] if _tracks else ".tmp/music.wav"


def short_term(path):
    r = subprocess.run(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128=metadata=0", "-f", "null", "-"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    pts = []
    for m in re.finditer(r"t:\s*([\d.]+)\s+TARGET.*?S:\s*(-?[\d.]+|-inf)", r.stderr):
        s = float(m.group(2)) if m.group(2) != "-inf" else -70.0
        pts.append((float(m.group(1)), max(s, -70.0)))
    return pts


if __name__ == "__main__":
    pts = short_term(MUSIC)
    # по секундам
    sec = {}
    for t, s in pts:
        sec.setdefault(int(t), []).append(s)
    prof = [(k, sum(v) / len(v)) for k, v in sorted(sec.items())]
    for k, s in prof:
        if k % 2 == 0:
            print(f"{k:4d}s {s:6.1f} " + "#" * max(0, int((s + 40) * 1.5)))
    cands = []
    for i in range(3, len(prof) - 3):
        before = sum(s for _, s in prof[i - 3:i]) / 3
        after = sum(s for _, s in prof[i:i + 3]) / 3
        cands.append((after - before, prof[i][0]))
    cands.sort(reverse=True)
    print("кандидаты дропа (прирост дБ, секунда):", [(round(d, 1), t) for d, t in cands[:8]])
