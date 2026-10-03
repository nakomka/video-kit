"""Нарезка лучших моментов для сравнения «до / после»: одинаковые куски из двух роликов,
ускорение, «сырой» цвет для «до». Звук не берётся — сравнение идёт под музыку.

python tools/highlights.py --raw <рез без графики.mp4> --edited <готовый.mp4> \\
       --segs "0.6:2.9 5.3:6.2 6.7:9.3" [--speed 1.6] [--out project/assets]

  --segs   куски в секундах ролика «после» (а:б через пробел): анимации, переходы, круг,
           телефон. НЕ брать энд-кард (чужое кодовое слово, лишний кадр на стыке).
  --speed  1.5–2 (по умолчанию 1.6)
Выход: <out>/raw.mp4 (цвет как у сырой записи) и <out>/edited.mp4 — кадр в кадр одной длины.
Оба исходника должны быть одной длины и синхронны (рез без графики + рендер того же реза).
"""
import subprocess
import sys

from common import ROOT

RAW_LOOK = "curves=all='0/0.07 0.5/0.5 1/0.93',eq=contrast=0.86:saturation=0.62:gamma=1.04,colorbalance=gs=0.02:bs=-0.02"


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


segs = arg("--segs").split()
speed = float(arg("--speed", 1.6))
out = arg("--out", "project/assets")


def build(src, dst, extra):
    f = [f"[0:v]split={len(segs)}" + "".join(f"[s{i}]" for i in range(len(segs)))]
    chain = ""
    for i, s in enumerate(segs):
        a, b = s.split(":")
        f.append(f"[s{i}]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]")
        chain += f"[v{i}]"
    f.append(chain + f"concat=n={len(segs)}:v=1:a=0,setpts=PTS/{speed},fps=30" + ("," + extra if extra else "") + "[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", ";".join(f), "-map", "[v]", "-an",
                    "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    dst], cwd=ROOT, check=True)


build(arg("--raw"), f"{out}/raw.mp4", RAW_LOOK)
build(arg("--edited"), f"{out}/edited.mp4", "")
total = sum(float(s.split(":")[1]) - float(s.split(":")[0]) for s in segs) / speed
print(f"{out}/raw.mp4, {out}/edited.mp4: {len(segs)} кусков, {total:.2f} с (×{speed})")
