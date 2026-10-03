"""Подставляет тайминги из cues.json в проект HyperFrames.

Что обновляется:
  project/index.html                 data-start / data-duration у хостов с data-clip="<имя>", длительность корня
  project/compositions/*.html        блок /*CUES:BEGIN*/…/*CUES:END*/ — C (глобальные метки) и T0 (старт сцены)
  project/compositions/captions.html генерируется целиком (субтитры-караоке)

python tools/cues_gen.py            # после любой перерезки (и после нового прогона whisper по cut16k.wav)
"""
import html
import json
import os
import re
import subprocess

from common import ROOT, load_json, norm_word, FPS

WORDS = ".tmp/cut_words_flat.json"
HTML = os.path.join(ROOT, "project", "index.html")
COMP = os.path.join(ROOT, "project", "compositions")
# видео в проекте (может быть дополнено стоп-кадром под энд-кард), иначе рез
VIDEO = "project/assets/cut.mp4" if os.path.exists(os.path.join(ROOT, "project", "assets", "cut.mp4")) else ".tmp/cut.mp4"

cfg = load_json("cues.json")
words = load_json(WORDS)
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                            "csv=p=0", VIDEO], cwd=ROOT, capture_output=True, text=True).stdout)
dur = round(int(dur * FPS) / FPS, 4)

# слитное тире ASR («–», «-») приклеиваем к предыдущему слову
merged = []
for w in words:
    if norm_word(w["w"]) == "" and merged:
        merged[-1]["w"] += " —"
        continue
    merged.append(dict(w))
words = merged


def find(word, n=1):
    key, k = norm_word(word), 0
    for i, w in enumerate(words):
        if norm_word(w["w"]) == key:
            k += 1
            if k == n:
                return i
    raise SystemExit(f"cue: слово «{word}» (n={n}) не найдено в {WORDS}")


def q(t):
    return round(round(t * FPS) / FPS, 4)


C = {}
for name, c in cfg["cues"].items():
    if "t" in c:
        t = dur if c["t"] == "duration" else float(c["t"])
    else:
        t = words[find(c["word"], c.get("n", 1))]["t"] + c.get("offset", 0.0)
    C[name] = q(max(0.0, min(dur, t)))

clips = {}
for name, c in cfg["clips"].items():
    a = max(0.0, C[c["from"]] - c.get("pre", 0.0))
    b = min(dur, C[c["to"]] + c.get("post", 0.0))
    clips[name] = (q(a), q(b - a))

# ---------- субтитры ----------
cap = cfg["captions"]
# цвета и шрифт субтитров: captions.accent / captions.ink / captions.font = {"family", "src"} в cues.json
ACCENT, INK = cap.get("accent", "#d62828"), cap.get("ink", "#14161a")
FONT = cap.get("font")
FONT_CSS = (f'@font-face {{ font-family: "{FONT["family"]}"; src: url("{FONT["src"]}"); font-weight: 200 900; }} '
            if FONT else "")
text = [w["w"] for w in words]
for fx in cap["fix"]:
    i = find(fx["word"], fx.get("n", 1))
    tail = re.search(r"[\W_]+$", text[i])
    new = fx["text"]
    if tail and not re.search(r"[.,!?:;—»]$", new):
        new += tail.group(0)
    text[i] = new
hide = [(C[a], C[b]) for a, b in cap.get("hide", [])]
scene_starts = sorted(v[0] for k, v in clips.items() if k not in ("head", "captions"))

groups, cur = [], []
for i, w in enumerate(words):
    t = w["t"]
    if any(a <= t < b for a, b in hide):
        continue
    if cur:
        prev = cur[-1]
        chars = sum(len(text[j]) + 1 for j in cur) + len(text[i])
        brk = (len(cur) >= cap["max_words"] or chars > cap["max_chars"]
               or re.search(r"[.?!]$", text[prev])
               or t - words[prev]["t"] > 0.9
               or any(words[prev]["t"] < s <= t for s in scene_starts))
        if brk:
            groups.append(cur)
            cur = []
    cur.append(i)
if cur:
    groups.append(cur)

caps = []
for gi, g in enumerate(groups):
    start = q(max(0, words[g[0]]["t"] - 0.06))
    last = words[g[-1]]["t"]
    nxt = q(words[groups[gi + 1][0]]["t"] - 0.06) if gi + 1 < len(groups) else dur
    end = min(nxt, q(last + 0.9), dur)
    for a, b in hide:
        if start < a < end:
            end = a
    caps.append({"s": start, "e": q(end), "w": [[text[j], q(words[j]["t"])] for j in g]})

cap_html = []
for k, c in enumerate(caps):
    spans = " ".join(f'<span class="cw" id="cw{k}_{j}">{html.escape(w)}</span>' for j, (w, _) in enumerate(c["w"]))
    cap_html.append(f'          <div class="cap" id="cap{k}">{spans}</div>')
cap_data = json.dumps([{"s": c["s"], "e": c["e"], "t": [w[1] for w in c["w"]]} for c in caps])
captions = f'''<!doctype html>
<html lang="ru">
  <head><meta charset="UTF-8" /></head>
  <body>
    <!-- СГЕНЕРИРОВАНО tools/cues_gen.py (слова: .tmp/cut_words_flat.json, правки текста: cues.json → captions.fix). Руками не править. -->
    <template>
      <link rel="stylesheet" href="styles.css" />
      <style>{FONT_CSS}#captions-root {{ position: absolute; inset: 0; }}</style>
      <div id="captions-root" data-composition-id="captions" data-width="1080" data-height="1920">
        <div class="capbox">
{chr(10).join(cap_html)}
        </div>
      </div>
      <script>
        (() => {{
          const CAPS = {cap_data};
          const tl = gsap.timeline({{ paused: true }});
          CAPS.forEach((c, k) => {{
            const id = "#cap" + k;
            tl.fromTo(id, {{ autoAlpha: 0, xPercent: -50, yPercent: -50, y: 14 }}, {{ autoAlpha: 1, xPercent: -50, yPercent: -50, y: 0, duration: 0.12, ease: "power2.out" }}, c.s);
            c.t.forEach((t, j) => {{
              const w = "#cw" + k + "_" + j;
              tl.set(w, {{ color: "{ACCENT}" }}, t);
              tl.set(w, {{ color: "{INK}" }}, j + 1 < c.t.length ? c.t[j + 1] : c.e);
            }});
            tl.set(id, {{ autoAlpha: 0 }}, c.e);
          }});
          window.__timelines["captions"] = tl;
        }})();
      </script>
    </template>
  </body>
</html>
'''
open(os.path.join(COMP, "captions.html"), "w", encoding="utf-8").write(captions)

# ---------- метки в сцены (локальное время: T0 = старт клипа сцены) ----------
for fn in sorted(os.listdir(COMP)):
    p = os.path.join(COMP, fn)
    s = open(p, encoding="utf-8").read()
    if "/*CUES:BEGIN*/" not in s:
        continue
    cid = re.search(r'data-composition-id="([^"]+)"', s).group(1)
    t0 = clips[cid][0] if cid in clips else 0.0
    s = re.sub(r"/\*CUES:BEGIN\*/.*?/\*CUES:END\*/",
               "/*CUES:BEGIN*/const C = " + json.dumps(C, ensure_ascii=False) + f"; const T0 = {t0};/*CUES:END*/",
               s, flags=re.S)
    open(p, "w", encoding="utf-8").write(s)

# ---------- корень: data-start / data-duration у хостов ----------
src = open(HTML, encoding="utf-8").read()
# блок меток в корне (раскладка спикера живёт в корневом таймлайне, T0 = 0)
src = re.sub(r"/\*CUES:BEGIN\*/.*?/\*CUES:END\*/",
             "/*CUES:BEGIN*/const C = " + json.dumps(C, ensure_ascii=False) + "; const T0 = 0;/*CUES:END*/",
             src, flags=re.S)
for name, (s, d) in clips.items():
    pat = re.compile(r'(<[^>]*data-clip="%s"[^>]*>)' % re.escape(name), re.S)
    m = pat.search(src)
    if not m:
        raise SystemExit(f"clip {name}: нет элемента data-clip в index.html")
    tag = m.group(1)
    tag2 = re.sub(r'data-start="[^"]*"', f'data-start="{s}"', tag)
    tag2 = re.sub(r'data-duration="[^"]*"', f'data-duration="{d}"', tag2)
    src = src.replace(tag, tag2)
src = re.sub(r'(id="root"[^>]*data-duration=")[^"]*(")', lambda m: f"{m.group(1)}{dur}{m.group(2)}", src, flags=re.S)
open(HTML, "w", encoding="utf-8").write(src)

print(f"duration {dur}; cues {len(C)}; clips {len(clips)}; caption groups {len(caps)}")
for name, (s, d) in clips.items():
    print(f"  {name:8s} {s:7.3f} +{d:.3f}")
