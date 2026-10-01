"""Сравнение уровней голоса и подложки на участке (маскирование окончаний музыкой).

python tools/mask_check.py 37.9 38.6
"""
import subprocess
import sys

import numpy as np

from common import ROOT

a, b = float(sys.argv[1]), float(sys.argv[2])


def env(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a), "-to", str(b), "-i", path, "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], cwd=ROOT, capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(float) / 32768
    n = 800  # 50 мс
    k = len(x) // n
    return 20 * np.log10(np.maximum(np.sqrt((x[:k * n].reshape(k, n) ** 2).mean(1)), 1e-6))


v, m = env(".tmp/voice.wav"), env(".tmp/bed_ducked.wav")
for i in range(min(len(v), len(m))):
    t = a + i * 0.05
    flag = "  ← музыка громче" if m[i] > v[i] - 3 and v[i] > -60 else ""
    print(f"{t:6.2f}  голос {v[i]:6.1f}  музыка {m[i]:6.1f}{flag}")
