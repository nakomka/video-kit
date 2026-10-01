<#
    Установка набора монтажа. Windows.

    Запуск:  правый клик → «Выполнить с помощью PowerShell»
    Или:     powershell -ExecutionPolicy Bypass -File setup\setup.ps1
    Ключи:   -Model large|medium|small   выбрать модель вручную (по умолчанию — по железу)
             -Force                      перекачать модель и whisper заново
             -Portable                   НИЧЕГО не ставить в систему: Node, Python и
                                         ffmpeg скачиваются портативными сборками внутрь
                                         папки набора. Не нужны winget и права админа,
                                         папку можно переносить вместе со всем содержимым.
                                         Исключение: Chrome для рендера (272 МБ) всё равно
                                         ложится в ~\.cache\hyperframes — так устроен
                                         HyperFrames, перенести его нельзя.

    Скрипт идемпотентный: всё, что уже стоит, он пропускает.
    Подробный лог — setup\last-run.log, итог — одна строка и файл kit.json.
#>
param(
    [ValidateSet("auto", "large", "medium", "small")] [string]$Model = "auto",
    [switch]$Force,
    [switch]$Portable
)

$ErrorActionPreference = "Stop"
# версия набора — из release.json (его же читают preflight и обновление)
$KitVersion = "1.1"
try { $KitVersion = (Get-Content (Join-Path $PSScriptRoot "..\release.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version } catch { }
$Kit = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $PSScriptRoot "last-run.log"
$WhisperRelease = "https://github.com/ggml-org/whisper.cpp/releases/download/v1.9.2/whisper-blas-bin-x64.zip"
$NodePortable   = "https://nodejs.org/dist/v22.20.0/node-v22.20.0-win-x64.zip"
$PythonEmbed    = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
$GetPip         = "https://bootstrap.pypa.io/get-pip.py"
$FfmpegPortable = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$ModelUrls = @{
    large  = @{ file = "ggml-large-v3-turbo.bin"; url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"; gb = 1.6; name = "large-v3-turbo" }
    medium = @{ file = "ggml-medium.bin";         url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin";         gb = 1.5; name = "medium" }
    small  = @{ file = "ggml-small.bin";          url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin";          gb = 0.5; name = "small" }
}

"=== Установка набора монтажа $KitVersion — $(Get-Date -Format 'yyyy-MM-dd HH:mm') ===" | Out-File $Log -Encoding utf8
function Say([string]$m) { Write-Host $m; $m | Out-File $Log -Append -Encoding utf8 }
function Run([string]$exe, [string[]]$a) {
    Say "  > $exe $($a -join ' ')"
    # npx и winget пишут прогресс в поток ошибок; при ErrorActionPreference=Stop
    # это валит скрипт, хотя команда отработала. Считаем только код возврата.
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $exe @a 2>&1 | Out-File $Log -Append -Encoding utf8 } catch { $_ | Out-File $Log -Append -Encoding utf8 }
    $ErrorActionPreference = $old
    $script:LastRunCode = $LASTEXITCODE
}
function Have([string]$c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }

# ---------- 0. железо ----------
$os = Get-CimInstance Win32_OperatingSystem
$ramGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
$freeGb = [math]::Round((Get-PSDrive ($Kit.Substring(0, 1))).Free / 1GB, 1)
Say "Папка набора: $Kit"
Say "ОЗУ: $ramGb ГБ; свободно на диске: $freeGb ГБ"

if ($Model -eq "auto") {
    # уже скачанная модель важнее выбора по железу: не качаем 1.5 ГБ повторно
    $already = @("large", "medium", "small") | Where-Object { Test-Path (Join-Path $Kit "models\$($ModelUrls[$_].file)") } | Select-Object -First 1
    if ($already -and -not $Force) {
        $Model = $already
        Say "Модель уже скачана, оставляю её"
    }
    # 8 ГБ ОЗУ система показывает как 7.8 — порог с запасом
    elseif ($ramGb -ge 7.5 -and $freeGb -ge 5) { $Model = "large" }
    elseif ($ramGb -ge 4 -and $freeGb -ge 3) { $Model = "medium" }
    else { $Model = "small" }
}
$M = $ModelUrls[$Model]
Say "Модель распознавания: $($M.name) ($($M.gb) ГБ)"
if ($Model -ne "large") {
    Say "  ВНИМАНИЕ: это не самая точная модель. Привязка графики к словам будет грубее,"
    Say "  субтитры потребуют больше правок. Форсировать большую: setup.ps1 -Model large"
}
if ($freeGb -lt ($M.gb + 2)) { Say "ОШИБКА: мало места на диске, нужно минимум $($M.gb + 2) ГБ"; exit 1 }

# ---------- 1. Node, ffmpeg, Python ----------
$ProgressPreference = "SilentlyContinue"
function Fetch([string]$url, [string]$outFile) {
    # curl.exe есть в Windows 10/11 и качает в разы быстрее, чем Invoke-WebRequest
    # (у IWR в PowerShell 5.1 полоса прогресса режет скорость до десятков КБ/с).
    $curlExe = Join-Path $env:SystemRoot "System32\curl.exe"
    if (Test-Path $curlExe) {
        & $curlExe -L --fail --silent --show-error --retry 3 --retry-delay 2 -o $outFile $url 2>&1 |
            Out-File $Log -Append -Encoding utf8
        if ($LASTEXITCODE -eq 0 -and (Test-Path $outFile) -and (Get-Item $outFile).Length -gt 0) { return }
        Remove-Item $outFile -Force -ErrorAction SilentlyContinue
    }
    $prev = $ProgressPreference; $ProgressPreference = "SilentlyContinue"
    try { Invoke-WebRequest $url -OutFile $outFile -UseBasicParsing } finally { $ProgressPreference = $prev }
}
function Unzip([string]$zip, [string]$dst) { New-Item -ItemType Directory -Force $dst | Out-Null; Expand-Archive $zip $dst -Force; Remove-Item $zip -Force }

$binDir = Join-Path $Kit "bin"
$pyExe = "python"; $nodeExe = "node"; $npxExe = "npx"; $ffExe = "ffmpeg"

if ($Portable) {
    Say "Портативный режим: ничего не ставлю в систему, всё кладу в $binDir"

    # Node
    $nodeHome = Get-ChildItem (Join-Path $binDir "node") -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Force -or -not $nodeHome) {
        Say "Качаю портативный Node.js (~30 МБ) …"
        $z = Join-Path $env:TEMP "node-portable.zip"; Fetch $NodePortable $z; Unzip $z (Join-Path $binDir "node")
        $nodeHome = Get-ChildItem (Join-Path $binDir "node") -Directory | Select-Object -First 1
    }
    $nodeExe = Join-Path $nodeHome.FullName "node.exe"
    $npxExe = Join-Path $nodeHome.FullName "npx.cmd"

    # Python (embeddable) + pip
    $pyHome = Join-Path $binDir "python"
    if ($Force -or -not (Test-Path (Join-Path $pyHome "python.exe"))) {
        Say "Качаю портативный Python (~11 МБ) …"
        $z = Join-Path $env:TEMP "python-embed.zip"; Fetch $PythonEmbed $z; Unzip $z $pyHome
        # embeddable-сборка по умолчанию не видит site-packages: включаем import site
        Get-ChildItem $pyHome -Filter "python*._pth" | ForEach-Object {
            (Get-Content $_.FullName) -replace '^#\s*import site', 'import site' | Set-Content $_.FullName -Encoding ascii
        }
        Say "Ставлю pip и numpy …"
        $gp = Join-Path $pyHome "get-pip.py"; Fetch $GetPip $gp
        Run (Join-Path $pyHome "python.exe") @($gp, "--no-warn-script-location")
        Remove-Item $gp -Force
    }
    $pyExe = Join-Path $pyHome "python.exe"
    Run $pyExe @("-m", "pip", "install", "--quiet", "numpy")

    # ffmpeg
    $ffHome = Get-ChildItem (Join-Path $binDir "ffmpeg") -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Force -or -not $ffHome) {
        Say "Качаю портативный ffmpeg (~115 МБ) …"
        $z = Join-Path $env:TEMP "ffmpeg-portable.zip"; Fetch $FfmpegPortable $z; Unzip $z (Join-Path $binDir "ffmpeg")
        $ffHome = Get-ChildItem (Join-Path $binDir "ffmpeg") -Directory | Select-Object -First 1
    }
    $ffBin = Join-Path $ffHome.FullName "bin"
    $ffExe = Join-Path $ffBin "ffmpeg.exe"
    $env:Path = "$ffBin;$($nodeHome.FullName);$pyHome;$env:Path"
    Say "Node, ffmpeg, Python — портативные, внутри набора"
}
else {
    $needWinget = @()
    if (-not (Have "node"))   { $needWinget += "OpenJS.NodeJS.LTS" }
    if (-not (Have "ffmpeg")) { $needWinget += "Gyan.FFmpeg" }
    if (-not (Have "python")) { $needWinget += "Python.Python.3.11" }

    if ($needWinget.Count -gt 0) {
        if (-not (Have "winget")) {
            Say "ОШИБКА: нет winget и не хватает: $($needWinget -join ', ')"
            Say "Либо поставь вручную (nodejs.org, ffmpeg.org, python.org), либо запусти"
            Say "портативный режим: setup.ps1 -Portable — он ничего не ставит в систему."
            exit 1
        }
        foreach ($p in $needWinget) {
            Say "Ставлю $p …"
            Run "winget" @("install", "--id", $p, "-e", "--accept-source-agreements", "--accept-package-agreements", "--silent")
        }
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    }
    foreach ($c in @("node", "ffmpeg", "ffprobe", "python")) {
        if (-not (Have $c)) { Say "ОШИБКА: $c не найден даже после установки. Перезапусти PowerShell и запусти скрипт ещё раз."; exit 1 }
    }
    Say "Node, ffmpeg, ffprobe, Python на месте"

    $hasNumpy = $false
    try { python -c "import numpy" 2>$null; $hasNumpy = $? } catch { $hasNumpy = $false }
    if (-not $hasNumpy) { Say "Ставлю numpy …"; Run "python" @("-m", "pip", "install", "--quiet", "numpy") }
}
Say "numpy на месте"

# кэш кадров рендера и кэш npm держим в наборе: во время рендера это гигабайты
$framesCache = Join-Path $Kit "cache\frames"
$npmCache = Join-Path $Kit "cache\npm"
New-Item -ItemType Directory -Force $framesCache, $npmCache | Out-Null
$env:HYPERFRAMES_EXTRACT_CACHE_DIR = $framesCache
if ($Portable) { $env:npm_config_cache = $npmCache }

# ---------- 3. whisper.cpp ----------
$whisperExe = Join-Path $Kit "bin\whisper\Release\whisper-cli.exe"
if ($Force -or -not (Test-Path $whisperExe)) {
    Say "Качаю whisper.cpp …"
    $zip = Join-Path $env:TEMP "whisper-bin.zip"
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest $WhisperRelease -OutFile $zip
    New-Item -ItemType Directory -Force (Join-Path $Kit "bin\whisper") | Out-Null
    Expand-Archive $zip (Join-Path $Kit "bin\whisper") -Force
    Remove-Item $zip -Force
}
if (-not (Test-Path $whisperExe)) { Say "ОШИБКА: whisper не распаковался"; exit 1 }
Say "whisper: $whisperExe"

# ---------- 4. модель ----------
$modelPath = Join-Path $Kit "models\$($M.file)"
New-Item -ItemType Directory -Force (Join-Path $Kit "models") | Out-Null
$needModel = $Force -or -not (Test-Path $modelPath) -or ((Get-Item $modelPath -ErrorAction SilentlyContinue).Length -lt 100MB)
if ($needModel) {
    Say "Качаю модель $($M.name), $($M.gb) ГБ. Это самый долгий шаг, 5–20 минут …"
    & curl.exe -L --fail --progress-bar -o $modelPath $M.url
    if ($LASTEXITCODE -ne 0) { Say "ОШИБКА: модель не скачалась"; exit 1 }
}
$modelMb = [math]::Round((Get-Item $modelPath).Length / 1MB)
Say "модель: $modelPath ($modelMb МБ)"

# ---------- 5. HyperFrames: skills и прогрев Chrome ----------
Say "Ставлю skills HyperFrames и прогреваю движок рендера (несколько минут) …"
Run $npxExe @("--yes", "hyperframes@latest", "skills", "update", "talking-head-recut", "embedded-captions")
Run $npxExe @("--yes", "hyperframes@latest", "doctor")

# ---------- 6. kit.json и env-файл ----------
function FirstLine([string]$exe, [string[]]$a) {
    try { (& $exe @a 2>&1 | Select-Object -First 1).ToString() } catch { "" }
}
$ffv = FirstLine $ffExe @("-version")
if ($ffv -match "ffmpeg version (\d+(\.\d+)*)") { $ffv = $Matches[1] }
$kitJson = [ordered]@{
    kit_version = $KitVersion
    installed_at = (Get-Date -Format "yyyy-MM-dd HH:mm")
    checked_at  = [int64](New-TimeSpan -Start ([datetime]"1970-01-01") -End ([datetime]::UtcNow)).TotalSeconds
    os          = "windows"
    portable    = [bool]$Portable
    whisper     = $whisperExe
    model       = $M.name
    model_path  = $modelPath
    ffmpeg      = $ffv
    ffmpeg_path = (Split-Path -Parent $ffExe)
    node        = (FirstLine $nodeExe @("--version")).TrimStart("v")
    node_path   = (Split-Path -Parent $nodeExe)
    python      = (FirstLine $pyExe @("--version"))
    python_path = $pyExe
    frames_cache = $framesCache
    npm_cache   = $npmCache
    ram_gb      = $ramGb
}
$kitJson | ConvertTo-Json | Set-Content (Join-Path $Kit "kit.json") -Encoding utf8
New-Item -ItemType Directory -Force (Join-Path $Kit "projects") | Out-Null

# env.ps1: подключает пути набора в текущую сессию PowerShell.
# В обычном режиме нужен только ради кэша кадров, в портативном — ещё и ради Node/Python/ffmpeg.
$envLines = @(
    "# Сгенерировано setup.ps1. Подключить: . `"$(Join-Path $Kit 'setup\env.ps1')`"",
    "`$env:HYPERFRAMES_EXTRACT_CACHE_DIR = `"$framesCache`""
)
if ($Portable) {
    $envLines += "`$env:npm_config_cache = `"$npmCache`""
    $envLines += "`$env:Path = `"$(Split-Path -Parent $ffExe);$(Split-Path -Parent $nodeExe);$(Split-Path -Parent $pyExe);`" + `$env:Path"
}
$envLines -join "`r`n" | Set-Content (Join-Path $Kit "setup\env.ps1") -Encoding utf8

Say ""
$mode = if ($Portable) { "портативный, всё внутри набора" } else { "обычный" }
Say "ГОТОВО. Набор $KitVersion, режим $mode, модель $($M.name), ОЗУ $ramGb ГБ."
Say "Кэш кадров рендера: $framesCache"
Say "Дальше: создай папку в projects\, положи туда видео и отправь короткий промпт из docs\3-TEMPLATES.txt"
if ($Model -ne "large") { Say "Модель не самая точная — субтитры и привязка графики потребуют больше правок." }
if ($Portable) { Say "Chrome для рендера (272 МБ) всё равно лежит в ~\.cache\hyperframes — так устроен HyperFrames." }
