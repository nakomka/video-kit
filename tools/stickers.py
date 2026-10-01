"""Нарезка листа со стикерами на отдельные PNG с прозрачным фоном.

Работает, когда фон листа светлый и стикеры не соприкасаются. Заливка фона от
краёв листа по «почти белым» пикселям упирается в контур стикера — всё, что не
залито, и есть стикер вместе с обводкой. Компоненты связности дают отдельные
стикеры, порядок — по рядам слева направо.

  python tools/stickers.py [лист.jpg] [папка_вывода] [--prefix=st] [--min-area=2500]

По умолчанию лист — первая картинка в папке проекта, вывод — project/assets/stickers.
Результат посмотри глазами: если фон не белый или стикеры слиплись, файлов
получится меньше, чем стикеров на листе, и скрипт об этом не догадается.
"""
import os
import subprocess
import sys
from collections import deque

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
_opts = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)

if _args:
    SRC = _args[0] if os.path.isabs(_args[0]) else os.path.join(ROOT, _args[0])
else:
    _sheets = [f for f in sorted(os.listdir(ROOT))
               if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png")]
    if not _sheets:
        raise SystemExit("не найден лист со стикерами: укажи файл первым аргументом")
    SRC = os.path.join(ROOT, _sheets[0])
OUT = _args[1] if len(_args) > 1 else os.path.join(ROOT, "project", "assets", "stickers")
PREFIX = _opts.get("prefix", "st")
MIN_AREA = int(_opts.get("min-area", 2500))


def load(path):
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", path],
                           capture_output=True, text=True).stdout.strip().split(",")
    w, h = int(probe[0]), int(probe[1])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)


def save_png(rgba, path):
    h, w = rgba.shape[:2]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                    "-i", "-", "-frames:v", "1", path], input=rgba.tobytes(), check=True)


def flood(passable, seeds):
    h, w = passable.shape
    seen = np.zeros_like(passable)
    q = deque()
    for y, x in seeds:
        if passable[y, x] and not seen[y, x]:
            seen[y, x] = True
            q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and passable[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((ny, nx))
    return seen


def label(mask):
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    n = 0
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not lab[y, x]:
                n += 1
                lab[y, x] = n
                q = deque([(y, x)])
                while q:
                    cy, cx = q.popleft()
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                            lab[ny, nx] = n
                            q.append((ny, nx))
    return lab, n


def erode(m, r):
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out &= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def main():
    img = load(SRC)
    h, w = img.shape[:2]
    white = img.min(axis=2) >= 244
    seeds = [(0, x) for x in range(w)] + [(h - 1, x) for x in range(w)] + \
            [(y, 0) for y in range(h)] + [(y, w - 1) for y in range(h)]
    bg = flood(white, seeds)
    fg = erode(~bg, 1)          # срезаем антиалиас-кайму
    lab, n = label(fg)
    os.makedirs(OUT, exist_ok=True)
    boxes = []
    for k in range(1, n + 1):
        ys, xs = np.nonzero(lab == k)
        if len(ys) < MIN_AREA:
            continue
        boxes.append((ys.min(), xs.min(), ys.max(), xs.max(), k, len(ys)))
    boxes.sort(key=lambda b: (b[0] // 120, b[1]))   # по рядам, слева направо
    for i, (y0, x0, y1, x1, k, area) in enumerate(boxes, 1):
        m = lab[y0:y1 + 1, x0:x1 + 1] == k
        rgba = np.zeros((y1 - y0 + 1, x1 - x0 + 1, 4), np.uint8)
        rgba[..., :3] = img[y0:y1 + 1, x0:x1 + 1]
        rgba[..., 3] = np.where(m, 255, 0)
        name = os.path.join(OUT, f"{PREFIX}{i:02d}.png")
        save_png(rgba, name)
        print(f"{PREFIX}{i:02d}.png  {x1 - x0 + 1}x{y1 - y0 + 1} at ({x0},{y0}) area {area}")


if __name__ == "__main__":
    main()
