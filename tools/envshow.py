"""Печать огибающей участка: python tools/envshow.py <src> <from> <to>"""
import sys

from common import load_json
from speechmask import speech_mask, HOP

sid, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
db = load_json(f".tmp/env{sid}.json")["db"]
sp = speech_mask(db)
for i in range(int(a / HOP), int(b / HOP)):
    print(f"{i*HOP:6.2f} {db[i]:6.1f} {'#' if sp[i] else '.'} " + "=" * max(0, int((db[i] + 60) / 1.5)))
