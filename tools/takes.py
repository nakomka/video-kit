"""Шаг 2, до EDL: все заходы на каждую фразу и лучший из них.

python tools/takes.py            # все исходники → печать + .tmp/takes.json + out/takes.md
python tools/takes.py --clips    # плюс видеофрагмент каждого захода в out/takes/ (послушать интонацию)

СПИСОК ДУБЛЕЙ (out/takes.md) показывается человеку ДО монтажа: фразы с несколькими
заходами, у каждого номер (1а, 1б…), время, текст и рекомендация ★. Человек выбирает
по интонации — его выбор идёт в EDL. Если у всех фраз один заход — одна строка.

Whisper по ЦЕЛОМУ файлу склеивает заходы в одну фразу: фальстарт «Отдаю абсолют…»
и сразу «Отдаю абсолютно бесплатно…» он пишет одним «Отдаю абсолютно бесплатно» (nnzalupa).
Поэтому исходник режется по паузам на отдельные высказывания, и каждое распознаётся само.

Для каждого высказывания:
  ФАЛЬСТАРТ  — следующее высказывание (в пределах 15 с) начинается так же, а это короче/рваное;
  ЗАПИНКА    — слово повторено подряд («все все»), обрыв на полуслове, паразит («э», «ээ», «ну это»);
  ОТМЕНА     — спикер сам себя отменяет («стоп», «заново», «не так», «нет, ещё раз»);
  ЛУЧШИЙ     — самый полный заход группы без запинок; при равенстве — последний.
Выход .tmp/takes.json читает edl_check.py: интервал EDL на неудачном заходе — ошибка.
"""
import difflib
import json
import os
import re
import subprocess
import sys

from common import ROOT, SOURCES, TMP, load_json, save_json, norm_word
from fragcheck import transcribe_piece
from speechmask import speech_mask, runs, HOP

PREP = {"для", "в", "во", "на", "с", "со", "к", "ко", "по", "о", "об", "от", "до", "из", "за", "под", "над", "при", "про", "у", "без"}
CONJ = {"и", "а", "но", "или"}
CANCEL = ("стоп", "заново", "ещё раз", "еще раз", "не так", "нет погоди", "переснимем", "сначала")
FILLERS = {"э", "ээ", "эээ", "эм", "мм", "ммм", "аа"}


def letters(t, n=None):
    s = re.sub(r"[^а-яёa-z]", "", t.lower().replace("ё", "е"))
    return s[:n] if n else s


def words(t):
    return re.findall(r"[а-яёa-z0-9]+", t.lower().replace("ё", "е"))


def gain_for(sid):
    edl = os.path.join(ROOT, "edl_spans.json")
    if os.path.exists(edl):
        g = load_json("edl_spans.json").get("analysis_gain_db", {}).get(sid)
        if g is not None:
            return float(g)
    r = subprocess.run(["ffmpeg", "-i", SOURCES[sid], "-af", "ebur128", "-f", "null", "-"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    i = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r)[-1])
    return -20.0 - i


def utterances(db, g):
    sp = speech_mask(db, gain=g)
    out = []
    for a, b in runs(sp):
        a, b = a * HOP, b * HOP
        if out and a - out[-1][1] < 0.35:  # паузы короче 0.35 с — внутри высказывания
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return [(a, b) for a, b in out if b - a >= 0.25]


def problems(text):
    w = words(text)
    p = []
    for x, y in zip(w, w[1:]):
        if x == y and len(x) > 1:
            p.append(f"повтор слова «{x} {y}»")
    if any(x in FILLERS for x in w):
        p.append("паразит «" + next(x for x in w if x in FILLERS) + "»")
    low = " ".join(w)
    for c in CANCEL:
        if re.search(r"\b" + c + r"\b", low):
            p.append(f"отмена «{c}»")
    if text.rstrip().endswith(("-", "—", "…")) and len(w) <= 3:
        p.append("обрыв")
    return p


cache_p = os.path.join(TMP, "takes_cache.json")
cache = load_json(".tmp/takes_cache.json") if os.path.exists(cache_p) else {}
report = {"utterances": [], "bad": []}
for sid in SOURCES:
    envp = os.path.join(TMP, f"env{sid}.json")
    wav = f".tmp/src{sid}_16k.wav"
    if not os.path.exists(envp):
        raise SystemExit(f"сначала tools/analyze.py ({sid})")
    db = load_json(f".tmp/env{sid}.json")["db"]
    utt = utterances(db, gain_for(sid))
    items = []
    for a, b in utt:
        key = f"{sid}|{a:.2f}|{b:.2f}"
        if key not in cache:
            cache[key] = transcribe_piece(wav, max(0, a - 0.15), b + 0.15, "tk")
        items.append({"src": sid, "a": round(a, 2), "b": round(b, 2), "text": cache[key],
                      "problems": problems(cache[key]), "mark": ""})
    # 1) по ОБЩЕЙ расшифровке: на целом файле whisper честно пишет «все все» и «для и»
    fw = load_json(f".tmp/words{sid}_flat.json") if os.path.exists(os.path.join(TMP, f"words{sid}_flat.json")) else []
    nw = [norm_word(x["w"]) for x in fw]
    for i in range(len(fw) - 1):
        nxt = fw[i + 2]["t"] if i + 2 < len(fw) else fw[i + 1]["t"] + 0.5
        if nw[i] == nw[i + 1] and len(nw[i]) > 1:
            why = f"ЗАПИНКА: повтор «{fw[i]['w']} {fw[i + 1]['w']}» — оставь одно"
            report["bad"].append([sid, fw[i + 1]["t"], round(nxt, 2), why])
            print(f"✗ {sid[-6:]} {fw[i + 1]['t']:6.2f}–{nxt:6.2f}  {why}")
        elif nw[i] in PREP and nw[i + 1] in CONJ:
            why = f"ЗАПИНКА: оборванный предлог «{fw[i]['w']} {fw[i + 1]['w']}»"
            report["bad"].append([sid, fw[i]["t"], round(nxt, 2), why])
            print(f"✗ {sid[-6:]} {fw[i]['t']:6.2f}–{nxt:6.2f}  {why}")
    # 2) кусок, слов которого нет в общем тексте: whisper выкинул этот заход из склейки — фальстарт
    vocab = set(nw)
    for u in items:
        ws = [x for x in words(u["text"]) if len(x) >= 3]
        if len(ws) >= 2 and vocab:
            found = sum(1 for x in ws if x in vocab or difflib.get_close_matches(x, vocab, n=1, cutoff=0.75))
            if found / len(ws) < 0.5:
                u["mark"] = f"ФАЛЬСТАРТ: заход не вошёл в общий текст (узнано {found}/{len(ws)} слов)"
    # 3) фальстарты: начало высказывания похоже на начало одного из следующих (до 15 с), а оно короче
    for i, u in enumerate(items):
        lu = letters(u["text"])
        if len(lu) < 4 or u["mark"]:
            continue
        for v in items[i + 1:i + 4]:
            if v["a"] - u["b"] > 15:
                break
            lv = letters(v["text"])
            n = min(len(lu), len(lv), 16)
            if n >= 4 and difflib.SequenceMatcher(None, lu[:n], lv[:n]).ratio() >= 0.6 and len(lu) <= len(lv) + 3:
                u["mark"] = f"ФАЛЬСТАРТ → лучший заход {v['a']:.2f}–{v['b']:.2f}"
                break
    for u in items:
        if not u["mark"]:
            u["mark"] = "ЗАПИНКА: " + "; ".join(u["problems"]) if u["problems"] else "ок"
        bad = u["mark"].startswith(("ФАЛЬСТАРТ", "ЗАПИНКА"))
        if bad:
            report["bad"].append([sid, u["a"], u["b"], u["mark"]])
        print(f"{'✗' if bad else ' '} {sid[-6:]} {u['a']:6.2f}–{u['b']:6.2f}  {u['mark']:42.42s}  {u['text'][:70]}")
    report["utterances"] += items
save_json(".tmp/takes_cache.json", cache)
save_json(".tmp/takes.json", report)
print(f"ДУБЛИ: {len(report['bad'])} неудачных высказываний → .tmp/takes.json (edl_check.py не пустит их в EDL)")

# ---------- список дублей для человека ----------
def mmss(t):
    return f"{int(t // 60)}:{t % 60:05.2f}"


groups, used = [], set()
U = report["utterances"]
for i, u in enumerate(U):
    if i in used or not u["mark"].startswith("ФАЛЬСТАРТ"):
        continue
    g, j = [i], i
    while U[j]["mark"].startswith("ФАЛЬСТАРТ") and j + 1 < len(U):
        m = re.search(r"заход ([\d.]+)–", U[j]["mark"])
        k = next((x for x in range(j + 1, len(U)) if m and abs(U[x]["a"] - float(m.group(1))) < 0.05), j + 1)
        g.append(k)
        j = k
    used.update(g)
    groups.append(g)
# фальстарт «не вошёл в общий текст» относится к следующему заходу
lines = ["# Дубли — выбери по интонации", ""]
clips = "--clips" in sys.argv
if clips:
    os.makedirs(os.path.join(ROOT, "out", "takes"), exist_ok=True)
letters_ab = "абвгдежз"
for n, g in enumerate(groups, 1):
    best = g[-1]
    lines.append(f"**Фраза {n}:** «{U[best]['text'][:60]}…»")
    for k, i in enumerate(g):
        u, tag = U[i], f"{n}{letters_ab[k]}"
        star = "★ рекомендую — целиком, без запинок" if i == best else "✗ " + u["mark"].split(":")[0].split("→")[0].strip().lower()
        line = f"- **{tag}** {mmss(u['a'])}–{mmss(u['b'])} «{u['text'][:70]}» — {star}"
        if clips:
            out = f"out/takes/{tag}.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, u['a'] - 0.2):.2f}", "-to", f"{u['b'] + 0.3:.2f}",
                            "-i", SOURCES[u["src"]], "-vf", "scale=540:-2", "-c:v", "libx264", "-crf", "26",
                            "-preset", "veryfast", "-c:a", "aac", "-b:a", "128k", out], cwd=ROOT, check=True)
            line += f" · [{tag}.mp4](takes/{tag}.mp4)"
        lines.append(line)
    lines.append("")
st = [b for b in report["bad"] if b[3].startswith("ЗАПИНКА")]
if st:
    lines.append("**Запинки внутри фраз — вырежу:** " + "; ".join(f"{mmss(a)} {why.split(': ', 1)[1]}" for _, a, _, why in st))
if not groups:
    lines.insert(2, "Каждая фраза сказана одним заходом — выбирать не из чего.")
if groups:
    lines.append("")
    lines.append("Напиши номера выбранных заходов (например «1а, 2б») или «как рекомендуешь».")
os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
open(os.path.join(ROOT, "out", "takes.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("СПИСОК ДУБЛЕЙ → out/takes.md" + (" (+ фрагменты out/takes/)" if clips else "") + " — покажи человеку до монтажа")
