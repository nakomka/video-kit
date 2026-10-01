"""Проверка повторов (ловушка 1): режем интервал по паузам на фрагменты <= 5 с
с разбегом из тишины и распознаём каждый отдельно.

python tools/fragcheck.py <wav16k> <env.json> <in> <out> [tag]
"""
import os
import sys

from common import TMP, load_json, run, WHISPER, MODEL, LANG
from speechmask import speech_mask, runs, HOP

MAXLEN = 5.0


def fragments(db, t_in, t_out):
    sp = speech_mask(db)
    i0, i1 = int(t_in / HOP), min(int(t_out / HOP), len(sp))
    gaps = [((i0 + a) * HOP, (i0 + b) * HOP) for a, b in runs(~sp[i0:i1]) if (b - a) * HOP >= 0.12]
    cuts = [t_in] + [(a + b) / 2 for a, b in gaps] + [t_out]
    frags, start = [], cuts[0]
    for i in range(1, len(cuts)):
        if cuts[i] - start > MAXLEN and cuts[i - 1] > start:
            frags.append((start, cuts[i - 1]))
            start = cuts[i - 1]
    frags.append((start, cuts[-1]))
    return frags


def transcribe_piece(wav, a, b, tag):
    piece = f".tmp/frag_{tag}.wav"
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", wav,
         "-af", "apad=pad_dur=0.5", "-c:a", "pcm_s16le", piece])
    r = run([WHISPER, "-m", MODEL, "-f", piece, "-l", LANG, "-t", "8", "-nt", "-np"])
    return " ".join(r.stdout.split())


if __name__ == "__main__":
    wav, envp, a, b = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
    tag = sys.argv[5] if len(sys.argv) > 5 else "x"
    db = load_json(envp)["db"]
    for k, (fa, fb) in enumerate(fragments(db, a, b)):
        print(f"  [{fa:6.2f}-{fb:6.2f}] {transcribe_piece(wav, fa, fb, f'{tag}_{k}')}")
