"""Проверка окружения одной командой. Печатает одну строку.

    python tools/preflight.py          # из папки проекта или из папки набора
    python tools/preflight.py --full   # игнорировать кэш проверки, проверить всё заново

Код возврата 0 — можно работать. 1 — надо запустить установщик.
Полная проверка кэшируется в kit.json на 14 дней: повторные запуски почти мгновенны.
Раз в сутки сверяет версию набора (release.json) с репозиторием автора. Если вышла новая,
дописывает в строку команду для Claude: обновить набор по UPDATE.md (автообновление).
"auto_update": false в kit.json — вместо этого только подсказка человеку.
"""
import json
import os
import shutil
import subprocess
import sys
import time

CACHE_DAYS = 14
UPDATE_CHECK_HOURS = 24


def _utf8_stdout():
    """Вывод в UTF-8: под Windows поток по умолчанию в cp1251/cp1252 и падает
    на кириллице и на знаке ✓, когда вывод уходит в файл или в другой процесс."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_utf8_stdout()


def find_kit(start=None):
    """Папка набора: переменная окружения, либо ближайшая вверх по дереву с kit.json,
    либо родитель папки, где лежит этот файл."""
    env = os.environ.get("VIDEO_KIT")
    if env and os.path.exists(os.path.join(env, "kit.json")):
        return env
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, "kit.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


KIT = find_kit()
KIT_JSON = os.path.join(KIT, "kit.json")


def load_kit():
    try:
        with open(KIT_JSON, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def load_release():
    try:
        with open(os.path.join(KIT, "release.json"), encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def _vtuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def update_note(kit, rel):
    """Подсказка о новой версии. Сеть — не чаще раза в сутки, таймаут 3 с, ошибки молчат:
    без интернета набор работает как обычно."""
    repo, branch, cur = rel.get("repo", ""), rel.get("branch", "main"), rel.get("version")
    if not repo or repo.startswith("OWNER/") or not cur:
        return ""
    latest = kit.get("latest_version")
    if time.time() - kit.get("update_checked_at", 0) > UPDATE_CHECK_HOURS * 3600:
        try:
            import urllib.request
            url = f"https://raw.githubusercontent.com/{repo}/{branch}/release.json"
            with urllib.request.urlopen(url, timeout=3) as r:
                latest = json.loads(r.read().decode("utf-8-sig")).get("version")
            kit["latest_version"] = latest
        except Exception:
            pass
        kit["update_checked_at"] = time.time()  # и при ошибке: без сети не ждать 3 с на каждом запуске
        save_kit(kit)
    if latest and _vtuple(latest) > _vtuple(cur):
        if kit.get("auto_update", True):
            return f" | вышла версия {latest}: обнови набор по UPDATE.md (автообновление)"
        return f" | вышла версия {latest}: скажите «обнови набор»"
    return ""


def save_kit(kit):
    try:
        with open(KIT_JSON, "w", encoding="utf-8") as f:
            json.dump(kit, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def ver(cmd, args=("--version",)):
    exe = shutil.which(cmd)
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        first = (r.stdout or r.stderr).strip().splitlines()[0]
        return first[:60]
    except Exception:
        return None


def ram_gb():
    try:
        if os.name == "nt":
            import ctypes

            class S(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            s = S()
            s.dwLength = ctypes.sizeof(S)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
            return s.ullTotalPhys / 2 ** 30, s.ullAvailPhys / 2 ** 30
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
        return total, total  # свободную на macOS точно не берём, для отчёта хватает общей
    except Exception:
        return 0.0, 0.0


def fail(msg):
    script = "setup\\setup.ps1" if os.name == "nt" else "setup/setup.command"
    print(f"FIX: {msg} → запусти {script} в папке набора ({KIT})")
    sys.exit(1)


def main():
    kit = load_kit()
    if not kit:
        fail("набор не установлен (нет kit.json)")

    whisper, model = kit.get("whisper", ""), kit.get("model_path", "")
    if not os.path.exists(whisper):
        fail("не найден whisper")
    if not os.path.exists(model):
        fail("не найдена модель распознавания")

    # портативный режим: бинарники внутри набора, системного PATH может не хватать
    for key in ("ffmpeg_path", "node_path"):
        p = kit.get(key)
        if p and os.path.isdir(p):
            os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
    mode = " портативный" if kit.get("portable") else ""
    rel = load_release()
    version = rel.get("version") or kit.get("kit_version")

    checked = kit.get("checked_at", 0)
    fresh = time.time() - checked < CACHE_DAYS * 86400 and "--full" not in sys.argv
    if fresh:
        total, free = ram_gb()
        print(f"KIT {version} OK{mode} | whisper ✓ {kit.get('model')} | "
              f"ffmpeg {kit.get('ffmpeg')} | node {kit.get('node')} | RAM {total:.1f}G, свободно {free:.1f}G"
              + update_note(kit, rel))
        return

    ffmpeg, node = ver("ffmpeg"), ver("node")
    if not ffmpeg:
        fail("не найден ffmpeg")
    if not node:
        fail("не найден Node.js")
    try:
        import numpy  # noqa: F401
    except ImportError:
        fail("не установлен numpy (pip install numpy)")

    kit["ffmpeg"] = ffmpeg.split()[2] if "version" in ffmpeg else ffmpeg
    kit["node"] = node.lstrip("v")
    kit["checked_at"] = time.time()
    save_kit(kit)
    total, free = ram_gb()
    print(f"KIT {version} OK{mode} | whisper ✓ {kit.get('model')} | "
          f"ffmpeg {kit['ffmpeg']} | node {kit['node']} | RAM {total:.1f}G, свободно {free:.1f}G"
          + update_note(kit, rel))


if __name__ == "__main__":
    main()
