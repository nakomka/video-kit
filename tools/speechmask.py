"""Маска речи и нарезка интервала на сегменты со сжатыми паузами.

Алгоритм по ТЗ:
  sdb[i]  = max(env[i-3..i+3])            скользящий максимум ±30 мс
  loud[i] = sdb[i] >= -30
  речь    = участки loud длиной >= 110 мс
  добор   = пока в пределах 250 мс дальше есть хоп с sdb >= -30, расширяем
  границы = первая речь - 20 мс, последняя + 30 мс
  паузы   >= 160 мс сжимаются до 50 мс (по 25 мс с краёв)
"""
import numpy as np

HOP = 0.010
THR = -30.0
MIN_RUN = 0.110
TAIL = 0.250
PAD_IN = 0.020
PAD_OUT = 0.030

DENSITY = {
    "максимальная": {"min_pause": 0.160, "keep": 0.050, "long_pause": 0.350, "tail_long": 0.110, "head_long": 0.040},
    "естественная": {"min_pause": 0.400, "keep": 0.220, "long_pause": 0.400, "tail_long": 0.110, "head_long": 0.110},
}


def smooth_max(db, r=3):
    db = np.asarray(db, dtype=float)
    pad = np.pad(db, r, mode="edge")
    return np.max(np.stack([pad[i:i + len(db)] for i in range(2 * r + 1)]), axis=0)


def runs(mask):
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append([start, i])
            start = None
    if start is not None:
        out.append([start, len(mask)])
    return out


DECAY_DB = 6.0      # над полом шума (10-й перцентиль огибающей)
DECAY_TAIL = 0.250  # максимум продления конца участка
DECAY_HEAD = 0.080  # максимум продления начала участка

RESCUE_THR = -40.0  # в шкале после усиления анализа
RESCUE_EDGE = 0.10


def rescue_words(speech, sdb, words):
    """Тихие слова ниже порога (напр. «тенге» на −38 dBFS): если ASR ставит начало
    слова глубоко внутри паузы маски, возвращаем в речь связный участок
    sdb >= RESCUE_THR вокруг этой точки. Whisper говорит «что», огибающая — «где»."""
    rescued = []
    edge = int(RESCUE_EDGE / HOP)
    for w in words or []:
        i = int(w["t"] / HOP)
        if i >= len(speech) or speech[i]:
            continue
        a = i
        while a > 0 and not speech[a - 1]:
            a -= 1
        b = i
        while b < len(speech) and not speech[b]:
            b += 1
        if i - a < edge or b - i < edge:
            continue  # у края паузы — это тайминг-дрейф, а не тихое слово
        # ищем ближайший к старту слова хоп над RESCUE_THR (в пределах ±200 мс)
        cands = [j for j in range(max(a, i - 20), min(b, i + 21)) if sdb[j] >= RESCUE_THR]
        if not cands:
            continue
        j = min(cands, key=lambda k: abs(k - i))
        lo = j
        while lo > a and sdb[lo - 1] >= RESCUE_THR:
            lo -= 1
        hi = j
        while hi < b and sdb[hi] >= RESCUE_THR:
            hi += 1
        if (hi - lo) * HOP >= MIN_RUN:
            speech[lo:hi] = True
            rescued.append((w["w"], round(lo * HOP, 2), round(hi * HOP, 2)))
    return rescued


def speech_mask(db, words=None, report=None, gain=0.0):
    # gain: огибающая приводится к уровню типичной сырой записи (−20 LUFS),
    # под который рассчитан порог −30 dBFS. Тихая запись без этого теряет
    # глухие начала слов.
    sdb = smooth_max(np.asarray(db, dtype=float) + gain)
    loud = sdb >= THR
    min_len = int(round(MIN_RUN / HOP))
    tail = int(round(TAIL / HOP))
    speech = np.zeros(len(db), dtype=bool)
    for a, b in runs(loud):
        if b - a >= min_len:
            speech[a:b] = True
    # добор глухих окончаний — внутри маски
    for a, b in runs(speech.copy()):
        k = b
        while True:
            ahead = np.nonzero(loud[k:k + tail])[0]
            if len(ahead) == 0:
                break
            nk = k + ahead[-1] + 1
            if nk <= k:
                break
            speech[k:nk] = True
            k = nk
    # затухание: глухие окончания («-нов», «-сь») и мягкие начала слов живут ниже порога,
    # но выше шума. Продлеваем участок, пока звук держится выше пола шума + DECAY_DB.
    raw = np.asarray(db, dtype=float)
    floor = float(np.percentile(raw, 10))
    for a, b in runs(speech.copy()):
        k = b
        while k < len(raw) and k - b < int(DECAY_TAIL / HOP) and raw[k] >= floor + DECAY_DB:
            k += 1
        speech[b:k] = True
        k = a
        while k > 0 and a - k < int(DECAY_HEAD / HOP) and raw[k - 1] >= floor + DECAY_DB:
            k -= 1
        speech[k:a] = True
    r = rescue_words(speech, sdb, words)
    if report is not None:
        report.extend(r)
    return speech


def to_segments(db, t_in, t_out, density="максимальная", fps=30, words=None, gain=0.0):
    """Возвращает список (a, b) в секундах источника для интервала [t_in, t_out]."""
    p = DENSITY[density]
    sp = speech_mask(db, words, gain=gain)
    i0, i1 = int(t_in / HOP), min(int(np.ceil(t_out / HOP)), len(sp))
    idx = np.nonzero(sp[i0:i1])[0]
    if len(idx) == 0:
        return []
    first = (i0 + idx[0]) * HOP - PAD_IN
    last = (i0 + idx[-1] + 1) * HOP + PAD_OUT
    segs = [[first, last]]
    half = p["keep"] / 2
    for a, b in runs(~sp[i0:i1]):
        ta, tb = (i0 + a) * HOP, (i0 + b) * HOP
        if ta <= first or tb >= last:
            continue
        if tb - ta >= p["min_pause"]:
            # паузы между фразами (длинные в исходнике) — чуть больше воздуха, чтобы конец фразы не глотался
            # после слова оставляем больше (там затухание), перед следующим — меньше
            ht, hh = (p["tail_long"], p["head_long"]) if tb - ta >= p["long_pause"] else (half, half)
            cur = segs[-1]
            segs[-1] = [cur[0], ta + ht]
            segs.append([tb - hh, cur[1]])
    q = lambda t: round(t * fps) / fps
    out = []
    for a, b in segs:
        a, b = q(max(a, 0)), q(b)
        if b - a >= 1.0 / fps:
            out.append((a, b))
    return out


def pauses(db, t_in, t_out, min_len=0.3, words=None, gain=0.0):
    """Паузы внутри интервала (без речи) длиной >= min_len — для проверки швов."""
    sp = speech_mask(db, words, gain=gain)
    i0, i1 = int(t_in / HOP), min(int(np.ceil(t_out / HOP)), len(sp))
    res = []
    for a, b in runs(~sp[i0:i1]):
        if a == 0 or b == i1 - i0:
            continue
        if (b - a) * HOP >= min_len:
            res.append((round((i0 + a) * HOP, 2), round((i0 + b) * HOP, 2)))
    return res
