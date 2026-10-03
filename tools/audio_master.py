"""Шаг 5: голос + музыка.

  голос:  хайпасс 80 Гц → двухпроходный loudnorm → −14 LUFS, TP −1 (деноиз не делаем: пол шума < −50 dBFS)
  музыка: срез трека так, чтобы дроп пришёлся на финальную фразу (cues: fs2Paid),
          нормализация к −24 LUFS (разнос 15 дБ), сайдчейн по голосу (ratio 3, thr 0.10, att 20, rel 380),
          apad на оба входа + atrim и сверка длительности (sidechaincompress теряет ~1 с хвоста)
  микс:   голос + подложка → двухпроходный loudnorm −14 LUFS, TP −1

python tools/audio_master.py [--bed -24] [--drop 49.3] [--drop-cue endIn]

  --drop      время дропа в треке; без него берётся .tmp/music_drop.txt (синтез) или ищется по громкости
  --drop-cue  метка из cues.json, на которую садится дроп (по умолчанию endIn — вход энд-карда)
  --bed-free  на сколько дБ поднять подложку там, где голоса нет дольше 1.5 с (по умолчанию 3;
              плавно, рампа 1.5 с — ступенька больше 6 дБ за секунду звучит как «музыка умерла»)
  --segments  файл стыков фраз для просадки подложки (по умолчанию .tmp/segments.json)
  --duck      smooth (по умолчанию): ровное приглушение под речью, рампы 0.4 с, без просадок на стыках;
              sidechain: старый компрессор (только под ритмичный бит — на тягучих треках «качает»)
  --duck-db   глубина плавного приглушения, дБ (по умолчанию 8 → разнос ≈ 15 дБ при --bed −21)
  Шумодав: пол шума голоса меряется ПОСЛЕ подъёма до −14 LUFS. Выше −50 dBFS → afftdn
  (тихая запись +17 дБ поднимает и шипение комнаты, под приглушённой музыкой оно слышно).
  Голос: .tmp/cut48k_pad.wav, если он есть (рез, дополненный тишиной до длины видео
  со стоп-кадром энд-карда), иначе .tmp/cut48k.wav.
"""
import json
import os
import re

import numpy as np
import subprocess
import sys

from common import ROOT, load_json, run
from music_fit import MUSIC

VOICE_IN = ".tmp/cut48k_pad.wav" if os.path.exists(os.path.join(ROOT, ".tmp", "cut48k_pad.wav")) else ".tmp/cut48k.wav"
VOICE = ".tmp/voice.wav"
BED = ".tmp/bed.wav"
MIX = ".tmp/audio_mixed.wav"
TARGET, TP = -14.0, -1.0
bed_lufs = float(sys.argv[sys.argv.index("--bed") + 1]) if "--bed" in sys.argv else -24.0


def ff(args):
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostats"] + args, cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def duration(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                       cwd=ROOT, capture_output=True, text=True)
    return float(r.stdout)


def loudnorm2(src, dst, pre="", target=TARGET, tp=TP, lra=11):
    """Двухпроходный loudnorm: замер → линейная нормализация по измеренным значениям."""
    chain = (pre + "," if pre else "")
    r = ff(["-i", src, "-af", f"{chain}loudnorm=I={target}:TP={tp}:LRA={lra}:print_format=json", "-f", "null", "-"])
    m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S).group(0))
    af = (f"{chain}loudnorm=I={target}:TP={tp}:LRA={lra}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}"
          f":linear=true,aresample=48000")
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", af, "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", dst])  # -ac 2: без раскладки каналов ffmpeg 6.0 падает
    return m


def measure(p):
    r = ff(["-i", p, "-af", "ebur128=peak=true", "-f", "null", "-"])
    i = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])
    tp = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)[-1])
    return i, tp


def find_drop(lo=46.0, hi=52.0):
    """Точный дроп: максимальный прирост моментальной громкости (0.1 с шаг, окна по 1 с)."""
    r = ff(["-ss", str(lo - 2), "-to", str(hi + 2), "-i", MUSIC, "-af", "ebur128=metadata=0", "-f", "null", "-"])
    pts = [(float(a) + lo - 2, float(b)) for a, b in re.findall(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+)", r.stderr)]
    best = (-99, lo)
    for i, (t, _) in enumerate(pts):
        if not (lo <= t <= hi):
            continue
        before = [m for tt, m in pts if t - 1.0 <= tt < t]
        after = [m for tt, m in pts if t <= tt < t + 1.0]
        if before and after:
            d = sum(after) / len(after) - sum(before) / len(before)
            if d > best[0]:
                best = (d, t)
    return best[1], best[0]


if __name__ == "__main__":
    cues = {}
    exec(open(f"{ROOT}/project/index.html", encoding="utf-8").read().split("/*CUES:BEGIN*/")[1]
         .split(";/*CUES:END*/")[0].replace("const C = ", "cues = ").replace("; const T0", "\nT0"), {}, cues)
    C = cues["cues"]
    vdur = duration(VOICE_IN)

    # --- голос ---
    # подъём до цели + лимитер по пикам (4× передискретизация ≈ true peak), затем точная линейная доводка
    r = ff(["-i", VOICE_IN, "-af", "highpass=f=80,ebur128", "-f", "null", "-"])
    in_i = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])
    g = TARGET - in_i + 0.5
    # пол шума голоса после подъёма (цифровую тишину дополнений не считаем)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", VOICE_IN, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         cwd=ROOT, capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    k = len(x) // 160
    db = 20 * np.log10(np.maximum(np.sqrt((x[: k * 160].reshape(k, 160) ** 2).mean(1)), 1e-9))
    # пол шума — из разбора исходников (.tmp/env*.json, там есть тишина), а не из реза:
    # в резе почти сплошная речь, её 10-й перцентиль — это речь, не шум
    import glob
    floors = [load_json(os.path.relpath(f, ROOT))["floor"] for f in glob.glob(os.path.join(ROOT, ".tmp", "env*.json"))]
    floor = max(floors) if floors else float(np.percentile(db[db > -100], 2))
    post = floor + g
    dn = ""
    if post > -50:
        nr = min(20.0, max(6.0, post + 56))
        dn = f"afftdn=nr={nr:.0f}:nf={max(-80, floor):.0f}:tn=1,"
    print(f"голос: пол шума {floor:.1f} dBFS, после подъёма {post:.1f} dBFS → "
          + (f"шумодав afftdn nr={nr:.0f}" if dn else "шумодав не нужен"))
    pre = (f"{dn}highpass=f=80,volume={g:.2f}dB,aresample=192000,"
           f"alimiter=limit=0.79:attack=3:release=60:level=false,aresample=48000")
    m = loudnorm2(VOICE_IN, VOICE, pre=pre)
    vi, vtp = measure(VOICE)
    print(f"голос: вход {m['input_i']} LUFS → {vi:.1f} LUFS, TP {vtp:.1f}")

    # --- музыка: срез по дропу ---
    if "--drop" in sys.argv:
        drop, gain = float(sys.argv[sys.argv.index("--drop") + 1]), 0
    elif MUSIC == ".tmp/music.wav" and os.path.exists(os.path.join(ROOT, ".tmp", "music_drop.txt")):
        drop, gain = float(open(os.path.join(ROOT, ".tmp", "music_drop.txt")).read()), 0
    else:
        drop, gain = find_drop()
    # дроп — сразу ПОСЛЕ финальной фразы, на вход CTA: на самом слове он глушил «окупилось»
    cue = sys.argv[sys.argv.index("--drop-cue") + 1] if "--drop-cue" in sys.argv else "endIn"
    off = drop - C[cue]
    if off < 0:
        raise SystemExit(f"дроп трека {drop:.2f} с раньше метки {cue} ({C[cue]:.2f} с): нужен трек длиннее "
                         f"(make_music.py --drop {C[cue] + 0.6:.1f})")
    print(f"дроп трека {drop:.2f} с (+{gain:.1f} дБ), {cue} {C[cue]:.2f} с → старт трека {off:.2f} с")
    fade_out = 1.2
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{off:.3f}", "-t", f"{vdur:.3f}", "-i", MUSIC,
         "-af", f"afade=t=in:d=0.4,afade=t=out:st={vdur - fade_out:.3f}:d={fade_out},aresample=48000",
         "-ac", "2", "-c:a", "pcm_s24le", ".tmp/bed_raw.wav"])
    loudnorm2(".tmp/bed_raw.wav", ".tmp/bed_norm0.wav", target=bed_lufs, tp=-3, lra=20)

    # защита окончаний: на каждом стыке фраз (конец сегмента реза) подложка плавно проседает на 8 дБ
    # за 0.2 с до конца слова и до 0.05 с после — удары трека не маскируют «-нов», «-сь»
    segs = load_json(sys.argv[sys.argv.index("--segments") + 1] if "--segments" in sys.argv else ".tmp/segments.json")
    ends, t = [], 0.0
    for s in segs:
        t += s["b"] - s["a"]
        if s.get("span") != "pad":  # вставки без речи (стоп-кадр, сравнение) — не стык фраз
            ends.append(t)
    smooth = not ("--duck" in sys.argv and sys.argv[sys.argv.index("--duck") + 1] == "sidechain")
    wins = "" if smooth else "+".join(f"clip((t-{e - 0.25:.3f})/0.05,0,1)*clip(({e + 0.05:.3f}-t)/0.05,0,1)" for e in ends[:-1])
    # где голоса нет дольше 1.5 с, подложка плавно поднимается на --bed-free дБ (рампа 1 с)
    free_db = float(sys.argv[sys.argv.index("--bed-free") + 1]) if "--bed-free" in sys.argv else 3.0
    loud = db + g > -45
    free, i = [], 0
    while i < len(loud):
        if not loud[i]:
            j = i
            while j < len(loud) and not loud[j]:
                j += 1
            if (j - i) * 0.01 >= 1.5:
                free.append((i * 0.01, j * 0.01))
            i = j
        else:
            i += 1
    R, K = 1.5, 10 ** (free_db / 20) - 1  # рампа 1.5 с: вместе с приглушением под голос провал ≤ 14 дБ
    ramps = "+".join((("1" if a < 0.05 else f"clip((t-{a:.3f})/{R},0,1)") + "*"
                      + ("1" if b > vdur - 0.05 else f"clip(({b - 0.3:.3f}-t)/{R},0,1)")) for a, b in free) or "0"
    print("без голоса: " + (", ".join(f"{a:.1f}–{b:.1f}" for a, b in free) or "нет") + f" → подложка +{free_db:.0f} дБ")
    run(["ffmpeg", "-y", "-v", "error", "-i", ".tmp/bed_norm0.wav", "-af",
         f"volume='(1-0.6*min(1,{wins or 0}))*(1+{K:.4f}*min(1,{ramps}))':eval=frame",
         "-c:a", "pcm_s24le", ".tmp/bed_norm.wav"])

    if "--duck" in sys.argv and sys.argv[sys.argv.index("--duck") + 1] == "sidechain":
        # --- сайдчейн: apad на оба входа, потом atrim — иначе хвост пропадает ---
        # два прохода вместо одного графа с двумя выходами: на ffmpeg 6.0 такой граф зависал
        graph = ("[1:a]aformat=channel_layouts=stereo,apad=pad_dur=2,asplit=2[key][vo];"
                 # ключ сайдчейна: тихие окончания слов поднимаем до уровня речи и держим 300 мс,
                 # иначе подложка всплывает на «-нов», «-сь» и маскирует их
                 "[key]compand=attacks=0.005:decays=0.35:points=-90/-90|-60/-60|-52/-6|0/-4[sc];"
                 "[0:a]aformat=channel_layouts=stereo,apad=pad_dur=2[bd];"
                 f"[bd][sc]sidechaincompress=threshold=0.10:ratio=3:attack=20:release=380,atrim=0:{vdur:.4f}[duck];"
                 f"[vo]atrim=0:{vdur:.4f}[v2];")
        run(["ffmpeg", "-y", "-v", "error", "-i", ".tmp/bed_norm.wav", "-i", VOICE, "-filter_complex",
             graph + "[v2][duck]amix=inputs=2:normalize=0:duration=first[mix]",
             "-map", "[mix]", "-c:a", "pcm_s24le", ".tmp/mix_raw.wav"])
        run(["ffmpeg", "-y", "-v", "error", "-i", ".tmp/bed_norm.wav", "-i", VOICE, "-filter_complex",
             graph + "[v2]anullsink",
             "-map", "[duck]", "-c:a", "pcm_s24le", ".tmp/bed_ducked.wav"])
    else:
        # ПЛАВНОЕ приглушение (по умолчанию): под речью подложка ровно ниже на DUCK дБ, спуск/подъём 0.4 с.
        # Сайдчейн-компрессор на тягучих треках «качает» ±5–10 дБ за доли секунды — звучит как поломка
        # (nnzalupa, slowed-трек). Под битом это не слышно — тогда можно --duck sidechain.
        duck_db = float(sys.argv[sys.argv.index("--duck-db") + 1]) if "--duck-db" in sys.argv else 8.0
        sp, i, regs = db + g > -45, 0, []
        while i < len(sp):
            if sp[i]:
                j = i
                while j < len(sp) and sp[j]:
                    j += 1
                a_, b_ = i * 0.01, j * 0.01
                if regs and a_ - regs[-1][1] < 1.2:  # паузы короче 1.2 с не выпускают музыку наверх
                    regs[-1] = (regs[-1][0], b_)
                else:
                    regs.append((a_, b_))
                i = j
            else:
                i += 1
        regs = [(a_, b_) for a_, b_ in regs if b_ - a_ > 0.15]
        D = 1 - 10 ** (-duck_db / 20)
        under = "+".join(f"clip((t-{a_ - 0.4:.3f})/0.4,0,1)*clip(({b_ + 0.4:.3f}-t)/0.4,0,1)" for a_, b_ in regs) or "0"
        print("речь (приглушение " + f"{duck_db:.0f} дБ): " + ", ".join(f"{a_:.1f}–{b_:.1f}" for a_, b_ in regs))
        run(["ffmpeg", "-y", "-v", "error", "-i", ".tmp/bed_norm.wav", "-af",
             f"aformat=channel_layouts=stereo,volume='1-{D:.4f}*min(1,{under})':eval=frame,apad=pad_dur=2,"
             f"atrim=0:{vdur:.4f}", "-ac", "2", "-c:a", "pcm_s24le", ".tmp/bed_ducked.wav"])
        run(["ffmpeg", "-y", "-v", "error", "-i", VOICE, "-i", ".tmp/bed_ducked.wav", "-filter_complex",
             f"[0:a]aformat=channel_layouts=stereo,apad=pad_dur=2,atrim=0:{vdur:.4f}[v2];"
             "[1:a]aformat=channel_layouts=stereo[bd];[v2][bd]amix=inputs=2:normalize=0:duration=first[mix]",
             "-map", "[mix]", "-c:a", "pcm_s24le", ".tmp/mix_raw.wav"])
    loudnorm2(".tmp/mix_raw.wav", MIX)

    mi, mtp = measure(MIX)
    bi, _ = measure(".tmp/bed_norm.wav")
    md = duration(MIX)
    di, _ = measure(".tmp/bed_ducked.wav")
    print(f"подложка {bi:.1f} LUFS (до сайдчейна), {di:.1f} LUFS под голосом → разнос {vi - di:.1f} дБ")
    print(f"микс: {mi:.1f} LUFS, TP {mtp:.1f} dBFS, длительность {md:.3f} с (голос {vdur:.3f} с, Δ {abs(md - vdur)*1000:.0f} мс)")
