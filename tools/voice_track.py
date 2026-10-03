"""Голосовая дорожка под длину ролика: тишина спереди и/или сзади — ТОЛЬКО этим скриптом.

python tools/voice_track.py --total 20.7 [--pre 14.3667] [--src .tmp/cut48k.wav] [--out .tmp/cut48k_pad.wav]

  --pre    тишина перед голосом, с (голос начинается позже: сравнение до/после, интро)
  --total  итоговая длина = длина видео (хвост добивается тишиной)

Почему не concat с anullsrc: на ffmpeg 6.0 склейка anullsrc + concat + s16 добавляет шипение
12–20 кГц на 18 дБ громче записи («пшшш» под всей речью, nnzalupa). adelay + apad в float
и 24 бита дают голос бит в бит как в резе. qa_audio.py сверяет спектр голоса с резом.
"""
import sys

from common import run


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


src = arg("--src", ".tmp/cut48k.wav")
out = arg("--out", ".tmp/cut48k_pad.wav")
pre = float(arg("--pre", 0))
total = float(arg("--total"))
ms = int(round(pre * 1000))
af = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono"
if ms:
    af += f",adelay=delays={ms}:all=1"
af += f",apad=whole_dur={total:.4f},atrim=0:{total:.4f},pan=stereo|c0=c0|c1=c0"
run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", af, "-c:a", "pcm_s24le", out])
print(f"{out}: голос с {pre:.3f} с, всего {total:.3f} с")
