"""Шаг 3: рез одним графом ffmpeg (trim/atrim + concat-фильтр, фейд 15 мс на стыках).

python tools/cut.py                 # .tmp/segments.json -> .tmp/cut.mp4
python tools/cut.py --audio out.wav # только звук (быстрая проверка)
"""
import sys
from collections import defaultdict

from common import SOURCES, load_json, run, save_json

FADE = 0.015
segs = load_json(".tmp/segments.json")
audio_only = "--audio" in sys.argv
out = sys.argv[sys.argv.index("--audio") + 1] if audio_only else ".tmp/cut.mp4"

srcs = sorted({s["src"] for s in segs}, key=int)
inp = {sid: k for k, sid in enumerate(srcs)}
uses = defaultdict(int)
for s in segs:
    uses[s["src"]] += 1

f = []
for sid in srcs:
    k, n = inp[sid], uses[sid]
    if not audio_only:
        f.append(f"[{k}:v]split={n}" + "".join(f"[v{sid}_{j}]" for j in range(n)))
    f.append(f"[{k}:a]asplit={n}" + "".join(f"[a{sid}_{j}]" for j in range(n)))

seen = defaultdict(int)
chain = []
t = 0.0
for i, s in enumerate(segs):
    sid, j = s["src"], seen[s["src"]]
    seen[sid] += 1
    a, b = s["a"], s["b"]
    d = b - a
    if not audio_only:
        f.append(f"[v{sid}_{j}]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS[v{i}]")
    f.append(f"[a{sid}_{j}]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,"
             f"afade=t=in:d={FADE},afade=t=out:st={d - FADE:.4f}:d={FADE}[a{i}]")
    chain.append((f"[v{i}]" if not audio_only else "") + f"[a{i}]")
    s["cut_in"] = round(t, 4)
    t += d
    s["cut_out"] = round(t, 4)
v = 0 if audio_only else 1
f.append("".join(chain) + f"concat=n={len(segs)}:v={v}:a=1" + ("[v][a]" if v else "[a]"))

cmd = ["ffmpeg", "-y", "-v", "error"]
for sid in srcs:
    cmd += ["-i", SOURCES[sid]]
cmd += ["-filter_complex", ";".join(f)]
if audio_only:
    cmd += ["-map", "[a]", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", out]
else:
    cmd += ["-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "16", "-preset", "slow",
            "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "1",
            "-movflags", "+faststart", out]
run(cmd)
if not audio_only:
    save_json(".tmp/segments.json", segs)
print(f"{out}: {len(segs)} сегментов, {t:.3f} с")
