"""Огибающая громкости: RMS по окнам 10 мс в dBFS.

python tools/envelope.py .tmp/src1_16k.wav .tmp/env1.json
"""
import sys
import wave

import numpy as np

from common import save_json

HOP = 0.010


def envelope(wav_path):
    with wave.open(wav_path, "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0
    n = int(round(sr * HOP))
    k = len(x) // n
    frames = x[: k * n].reshape(k, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    return np.round(db, 2)


def noise_floor(db):
    """Пол шума: 10-й перцентиль огибающей."""
    return float(np.percentile(db, 10))


if __name__ == "__main__":
    db = envelope(sys.argv[1])
    save_json(sys.argv[2], {"hop": HOP, "db": db.tolist(), "floor": noise_floor(db)})
    print(f"{sys.argv[1]}: {len(db)} hops, floor {noise_floor(db):.1f} dBFS, peak {db.max():.1f}")
