"""Листы кадров на каждом стыке клипов — смотреть ГЛАЗАМИ перед выдачей.

python tools/qa_frames.py out/master.mp4

Берёт из project/index.html начала и концы всех клипов (data-start, data-start + data-duration),
снимает кадры за −0.3…+0.3 с вокруг каждого стыка и складывает в .tmp/qa_frames/стык_<t>.jpg.
Плюс автоматически: чёрные кадры (blackdetect) и залипшие кадры дольше 0.6 с (freezedetect).

Что искать глазами (всё это уже случалось):
  - пустые/тёмные карточки или окна: видео кончилось раньше, чем ушла его рамка;
  - субтитры или графика следующей части раньше её видео;
  - чужой кадр на стыке (энд-кард прошлого ролика, стоп-кадр, взмах руки до первого слова).
"""
import os
import re
import subprocess
import sys

from common import ROOT

video = sys.argv[1]
html = open(os.path.join(ROOT, "project", "index.html"), encoding="utf-8").read()
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                           cwd=ROOT, capture_output=True, text=True).stdout)
ts = set()
for m in re.finditer(r'data-start="([\d.]+)"[^>]*?data-duration="([\d.]+)"', html):
    a, d = float(m.group(1)), float(m.group(2))
    for t in (a, a + d):
        if 0.3 < t < dur - 0.3:
            ts.add(round(t, 2))
out = os.path.join(ROOT, ".tmp", "qa_frames")
os.makedirs(out, exist_ok=True)
for f in os.listdir(out):
    os.remove(os.path.join(out, f))
for t in sorted(ts):
    times = [t + k * 0.1 for k in range(-3, 4)]
    args, fl = [], []
    for i, x in enumerate(times):
        args += ["-ss", f"{x:.3f}", "-i", video]
        fl.append(f"[{i}:v]scale=200:-1,setpts=PTS-STARTPTS,trim=end_frame=1[f{i}]")
    fl.append("".join(f"[f{i}]" for i in range(len(times))) + f"hstack={len(times)}[o]")
    subprocess.run(["ffmpeg", "-v", "error", "-y"] + args + ["-filter_complex", ";".join(fl), "-map", "[o]",
                    "-frames:v", "1", os.path.join(out, f"стык_{t:06.2f}.jpg")], cwd=ROOT, check=True)
r = subprocess.run(["ffmpeg", "-i", video, "-vf", "blackdetect=d=0.05:pix_th=0.08,freezedetect=n=0.002:d=0.6",
                    "-an", "-f", "null", "-"], cwd=ROOT, capture_output=True, text=True).stderr
black = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r)
freeze = re.findall(r"freeze_start: ([\d.]+).*?freeze_duration: ([\d.]+)", r, re.S)
print(f"стыков: {len(ts)} → .tmp/qa_frames/ (смотри каждый лист)")
for a, b in black:
    print(f"  ✗ чёрный кадр {a}–{b} с")
for a, d in freeze:
    print(f"  ! залипание {float(a):.2f} с на {float(d):.2f} с — проверь, задумано ли (стоп-кадр энд-карда)")
