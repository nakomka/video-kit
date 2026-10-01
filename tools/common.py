"""Общие пути и утилиты для инструментов монтажа.

Пути к whisper и модели берутся из kit.json набора (его пишет установщик),
а не зашиты в код: набор может лежать где угодно.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # папка проекта
TMP = os.path.join(ROOT, ".tmp")
LANG = "ru"
FPS = 30


def _utf8_stdout():
    """Вывод в UTF-8: под Windows поток по умолчанию в cp1251/cp1252 и падает
    на кириллице и на знаке ✓, когда вывод уходит в файл или в другой процесс."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_utf8_stdout()


def _find_kit():
    env = os.environ.get("VIDEO_KIT")
    if env and os.path.exists(os.path.join(env, "kit.json")):
        return env
    d = ROOT
    while True:
        if os.path.exists(os.path.join(d, "kit.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


KIT = _find_kit()
_kit = {}
if KIT:
    try:
        with open(os.path.join(KIT, "kit.json"), encoding="utf-8-sig") as f:
            _kit = json.load(f)
    except Exception:
        _kit = {}

WHISPER = _kit.get("whisper", "whisper-cli")
MODEL = _kit.get("model_path", "")
MODEL_NAME = _kit.get("model", "?")

# Портативный режим: Node, Python и ffmpeg лежат внутри набора и в системном PATH
# их нет. Добавляем пути набора в PATH процесса, а кэш кадров рендера уводим
# с системного диска (во время рендера это гигабайты).
for _key in ("ffmpeg_path", "node_path"):
    _p = _kit.get(_key)
    if _p and os.path.isdir(_p) and _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + os.pathsep + os.environ.get("PATH", "")
if _kit.get("frames_cache"):
    os.environ.setdefault("HYPERFRAMES_EXTRACT_CACHE_DIR", _kit["frames_cache"])
if _kit.get("portable") and _kit.get("npm_cache"):
    os.environ.setdefault("npm_config_cache", _kit["npm_cache"])

# Исходники проекта: все видеофайлы папки по алфавиту. Порядок склейки задаёт
# человек, а скрипты берут src из edl_spans.json.
SOURCES = {}
if os.path.isdir(ROOT):
    for _f in sorted(os.listdir(ROOT)):
        if os.path.splitext(_f)[1].lower() in (".mp4", ".mov", ".mkv"):
            SOURCES[os.path.splitext(_f)[0]] = _f


def run(cmd, **kw):
    """Запуск из корня проекта. Пути к медиа передавай ОТНОСИТЕЛЬНЫМИ:
    whisper.cpp не открывает файлы, если в абсолютном пути есть кириллица."""
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **kw)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit(f"command failed: {cmd[0]}")
    return r


def extract_wav(src, dst, rate=16000, extra=None):
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if extra:
        cmd += extra
    cmd += ["-i", src, "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", dst]
    run(cmd)


def whisper_text(wav, out_base, lang=LANG):
    run([WHISPER, "-m", MODEL, "-f", wav, "-l", lang, "-t", "8",
         "-ojf", "-osrt", "-of", out_base])


def whisper_words(wav, out_base, lang=LANG):
    """Словные тайминги. -dtw обязателен, иначе t_dtw = -1 и слова разъезжаются
    на секунды. Модель в -dtw должна совпадать с моделью распознавания."""
    dtw = {"large-v3-turbo": "large.v3.turbo", "medium": "medium", "small": "small",
           "base": "base", "tiny": "tiny"}.get(MODEL_NAME, "large.v3.turbo")
    run([WHISPER, "-m", MODEL, "-f", wav, "-l", lang, "-t", "8",
         "-dtw", dtw, "-ml", "1", "-sow", "-oj", "-of", out_base])


def load_json(path):
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
        return json.load(f)


def save_json(path, obj):
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def norm_word(w):
    """Сравнение слов без краевой пунктуации и регистра: whisper между прогонами
    пишет то «эти», то «эти.», то «Let's», то «let's»."""
    return _PUNCT.sub("", w.strip()).lower().replace("ё", "е")


def words_from_whisper(path):
    """Слова со стартовыми таймингами (DTW). При -ml 1 конец слова = начало
    следующего, поэтому осмысленны только стартовые тайминги."""
    data = load_json(path)
    out = []
    for seg in data.get("transcription", []):
        toks = seg.get("tokens", [])
        text = seg.get("text", "").strip()
        if not text or text.startswith("["):
            continue
        t = None
        for tk in toks:
            if tk.get("text", "").startswith("[_"):
                continue
            if tk.get("t_dtw", -1) not in (-1, None):
                t = tk["t_dtw"] / 100.0
                break
        if t is None:
            t = seg["offsets"]["from"] / 1000.0
        out.append({"w": text, "t": round(t, 3)})
    return out


def frame(t):
    return round(t * FPS) / FPS
