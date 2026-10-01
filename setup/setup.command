#!/bin/bash
# Установка набора монтажа. macOS.
#
# Запуск: двойной клик по файлу (может потребоваться «Открыть» через контекстное меню),
#         либо в терминале:  bash setup/setup.command
# Ключи:  MODEL=large|medium|small  — выбрать модель вручную (по умолчанию по железу)
#         FORCE=1                   — перекачать whisper и модель заново
#         PORTABLE=1                — Node, ffmpeg и Python держать внутри папки набора,
#                                     а не ставить в систему. ВАЖНО: whisper.cpp на macOS
#                                     всё равно ставится через brew — готовых бинарников
#                                     под macOS в релизах whisper.cpp нет.
#
# ВНИМАНИЕ: этот установщик написан по документации и НЕ протестирован на живом Mac.
# Если что-то пошло не так — покажите вывод Claude Code, он починит.

set -u
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# версия набора — из release.json (его же читают preflight и обновление)
KIT_VERSION="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$KIT/release.json" 2>/dev/null | head -1)"
KIT_VERSION="${KIT_VERSION:-1.1}"
LOG="$KIT/setup/last-run.log"
MODEL="${MODEL:-auto}"
FORCE="${FORCE:-0}"
PORTABLE="${PORTABLE:-0}"

echo "=== Установка набора монтажа $KIT_VERSION — $(date '+%Y-%m-%d %H:%M') ===" > "$LOG"
say() { echo "$1"; echo "$1" >> "$LOG"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---------- 0. железо ----------
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
FREE_GB=$(df -g "$KIT" | awk 'NR==2 {print $4}')
say "Папка набора: $KIT"
say "ОЗУ: ${RAM_GB} ГБ; свободно на диске: ${FREE_GB} ГБ"

if [ "$MODEL" = "auto" ]; then
  if [ "$RAM_GB" -ge 8 ] && [ "$FREE_GB" -ge 5 ]; then MODEL=large
  elif [ "$RAM_GB" -ge 4 ] && [ "$FREE_GB" -ge 3 ]; then MODEL=medium
  else MODEL=small; fi
fi
case "$MODEL" in
  large)  MODEL_FILE=ggml-large-v3-turbo.bin; MODEL_NAME=large-v3-turbo; NEED_GB=4 ;;
  medium) MODEL_FILE=ggml-medium.bin;         MODEL_NAME=medium;         NEED_GB=3 ;;
  small)  MODEL_FILE=ggml-small.bin;          MODEL_NAME=small;          NEED_GB=2 ;;
esac
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODEL_FILE"
say "Модель распознавания: $MODEL_NAME"
[ "$MODEL" != "large" ] && say "  ВНИМАНИЕ: не самая точная модель, субтитры потребуют больше правок. Форсировать: MODEL=large bash setup/setup.command"
if [ "$FREE_GB" -lt "$NEED_GB" ]; then say "ОШИБКА: мало места на диске, нужно минимум ${NEED_GB} ГБ"; exit 1; fi

# ---------- 1. Homebrew, Node, ffmpeg, Python, whisper ----------
# свежий brew на Apple Silicon лежит в /opt/homebrew и не всегда попадает в PATH скрипта
if ! have brew; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)" && break; done
fi
if ! have brew; then
  say "ОШИБКА: не установлен Homebrew. Поставь его командой с brew.sh и запусти скрипт заново."
  exit 1
fi

if [ "$PORTABLE" = "1" ]; then
  say "Портативный режим: Node, ffmpeg и Python кладу внутрь набора"
  ARCH=$(uname -m); [ "$ARCH" = "arm64" ] && NARCH=arm64 || NARCH=x64
  NODE_VER=v22.20.0
  mkdir -p "$KIT/bin"
  if [ ! -x "$KIT/bin/node/bin/node" ]; then
    say "Качаю портативный Node.js …"
    curl -L --fail --progress-bar -o /tmp/node.tar.gz \
      "https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-darwin-$NARCH.tar.gz" || { say "ОШИБКА: Node не скачался"; exit 1; }
    rm -rf "$KIT/bin/node"; mkdir -p "$KIT/bin/node"
    tar -xzf /tmp/node.tar.gz -C "$KIT/bin/node" --strip-components=1 && rm /tmp/node.tar.gz
  fi
  export PATH="$KIT/bin/node/bin:$PATH"
  # ffmpeg: пакет npm с готовыми статическими бинарниками — брать неоткуда больше
  if [ ! -x "$KIT/bin/ffmpeg/ffmpeg" ]; then
    say "Качаю портативный ffmpeg …"
    (cd "$KIT/bin" && npm install --silent --prefix "$KIT/bin/ffmpeg-pkg" ffmpeg-static ffprobe-static >> "$LOG" 2>&1)
    mkdir -p "$KIT/bin/ffmpeg"
    cp "$KIT/bin/ffmpeg-pkg/node_modules/ffmpeg-static/ffmpeg" "$KIT/bin/ffmpeg/" 2>/dev/null
    # в пакете ffprobe под все платформы — берём ровно под этот Mac
    cp "$KIT/bin/ffmpeg-pkg/node_modules/ffprobe-static/bin/darwin/$NARCH/ffprobe" "$KIT/bin/ffmpeg/" 2>/dev/null
    chmod +x "$KIT/bin/ffmpeg/"* 2>/dev/null
  fi
  export PATH="$KIT/bin/ffmpeg:$PATH"
  # Python: venv на системном python3 (он есть в macOS из коробки)
  if [ ! -x "$KIT/bin/python/bin/python3" ]; then
    say "Делаю окружение Python внутри набора …"
    python3 -m venv "$KIT/bin/python" >> "$LOG" 2>&1 || { say "ОШИБКА: не создалось окружение Python"; exit 1; }
  fi
  export PATH="$KIT/bin/python/bin:$PATH"
  "$KIT/bin/python/bin/python3" -m pip install --quiet numpy >> "$LOG" 2>&1
  # whisper всё равно через brew: готовых бинарников под macOS в релизах нет
  have whisper-cli || { say "Ставлю whisper-cpp через brew (в портативном режиме иначе никак) …"; brew install whisper-cpp >> "$LOG" 2>&1; }
  PORTABLE_DONE=1
fi

for pkg in node ffmpeg python@3.11 whisper-cpp; do
  [ "${PORTABLE_DONE:-0}" = "1" ] && break
  bin="$pkg"; [ "$pkg" = "python@3.11" ] && bin=python3
  [ "$pkg" = "whisper-cpp" ] && bin=whisper-cli
  if have "$bin"; then
    say "$pkg уже стоит"
  else
    say "Ставлю $pkg …"
    brew install "$pkg" >> "$LOG" 2>&1 || { say "ОШИБКА при установке $pkg, смотри $LOG"; exit 1; }
  fi
done
for c in node ffmpeg ffprobe python3 whisper-cli; do
  have "$c" || { say "ОШИБКА: $c не найден после установки"; exit 1; }
done
say "Node, ffmpeg, Python, whisper на месте"

# старый pip (системный python3 macOS) не знает --break-system-packages — тогда ставим в --user
python3 -c "import numpy" 2>/dev/null || { say "Ставлю numpy …"; python3 -m pip install --quiet --break-system-packages numpy >> "$LOG" 2>&1 || python3 -m pip install --quiet --user numpy >> "$LOG" 2>&1; }
python3 -c "import numpy" 2>/dev/null || { say "ОШИБКА: numpy не поставился, смотри $LOG"; exit 1; }

# ---------- 2. модель ----------
mkdir -p "$KIT/models" "$KIT/projects"
MODEL_PATH="$KIT/models/$MODEL_FILE"
if [ "$FORCE" = "1" ] || [ ! -s "$MODEL_PATH" ]; then
  say "Качаю модель $MODEL_NAME. Это самый долгий шаг, 5–20 минут …"
  curl -L --fail --progress-bar -o "$MODEL_PATH" "$MODEL_URL" || { say "ОШИБКА: модель не скачалась"; exit 1; }
fi
say "модель: $MODEL_PATH"

# ---------- 3. HyperFrames ----------
say "Ставлю skills HyperFrames и прогреваю движок рендера …"
npx --yes hyperframes@latest skills update talking-head-recut embedded-captions >> "$LOG" 2>&1
npx --yes hyperframes@latest browser ensure >> "$LOG" 2>&1 || say "ВНИМАНИЕ: Chrome для рендера не поставился, смотри $LOG"
npx --yes hyperframes@latest doctor >> "$LOG" 2>&1

# ---------- 4. kit.json ----------
FRAMES_CACHE="$KIT/cache/frames"; NPM_CACHE="$KIT/cache/npm"
mkdir -p "$FRAMES_CACHE" "$NPM_CACHE"
PORTABLE_FLAG=false; [ "${PORTABLE_DONE:-0}" = "1" ] && PORTABLE_FLAG=true

cat > "$KIT/kit.json" <<EOF
{
 "kit_version": "$KIT_VERSION",
 "installed_at": "$(date '+%Y-%m-%d %H:%M')",
 "checked_at": $(date +%s),
 "os": "macos",
 "portable": $PORTABLE_FLAG,
 "whisper": "$(command -v whisper-cli)",
 "model": "$MODEL_NAME",
 "model_path": "$MODEL_PATH",
 "ffmpeg": "$(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')",
 "ffmpeg_path": "$(dirname "$(command -v ffmpeg)")",
 "node": "$(node --version | tr -d v)",
 "node_path": "$(dirname "$(command -v node)")",
 "python": "$(python3 --version)",
 "python_path": "$(command -v python3)",
 "frames_cache": "$FRAMES_CACHE",
 "npm_cache": "$NPM_CACHE",
 "ram_gb": $RAM_GB
}
EOF

# env.sh: подключает окружение набора в текущую сессию терминала
{
  echo "# Сгенерировано setup.command. Подключить: source $KIT/setup/env.sh"
  echo "export HYPERFRAMES_EXTRACT_CACHE_DIR=\"$FRAMES_CACHE\""
  if [ "${PORTABLE_DONE:-0}" = "1" ]; then
    echo "export npm_config_cache=\"$NPM_CACHE\""
    echo "export PATH=\"$KIT/bin/node/bin:$KIT/bin/ffmpeg:$KIT/bin/python/bin:\$PATH\""
  fi
} > "$KIT/setup/env.sh"

say ""
say "ГОТОВО. Набор $KIT_VERSION, модель $MODEL_NAME, ОЗУ ${RAM_GB} ГБ."
say "Дальше: создай папку в projects/, положи туда видео и отправь короткий промпт из docs/3-TEMPLATES.txt"
