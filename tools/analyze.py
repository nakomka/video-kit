"""Шаг 1: для каждого исходника — wav 16k, расшифровка, словные тайминги, огибающая.

python tools/analyze.py            # все 1..5
python tools/analyze.py 3          # один
"""
import os
import sys

from common import SOURCES, TMP, extract_wav, whisper_text, whisper_words, save_json, words_from_whisper
from envelope import envelope, noise_floor
from speechmask import speech_mask, runs, HOP

ids = sys.argv[1:] or list(SOURCES)
for sid in ids:
    src = SOURCES[sid]
    wav = f".tmp/src{sid}_16k.wav"
    extract_wav(src, wav)
    whisper_text(wav, f".tmp/transcript{sid}")
    whisper_words(wav, f".tmp/words{sid}")
    db = envelope(os.path.join(TMP, f"src{sid}_16k.wav"))
    save_json(f".tmp/env{sid}.json", {"hop": HOP, "db": db.tolist(), "floor": noise_floor(db)})
    sp = speech_mask(db)
    sil = [((a * HOP), (b * HOP)) for a, b in runs(~sp) if (b - a) * HOP >= 0.3]
    words = words_from_whisper(os.path.join(TMP, f"words{sid}.json"))
    save_json(f".tmp/words{sid}_flat.json", words)
    print(f"== {src}: floor {noise_floor(db):.1f} dBFS, {len(words)} words")
    print("   silences>=0.3s:", ", ".join(f"{a:.2f}-{b:.2f}" for a, b in sil))
