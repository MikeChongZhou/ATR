$ErrorActionPreference = "Stop"

$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
if (Test-Path ".venv\Scripts\python.exe") {
    $Python = (Resolve-Path ".venv\Scripts\python.exe").Path
}
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
& $Python -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed" }
& $Python -m PyInstaller `
    --noconfirm `
    --onedir `
    --windowed `
    --name LocalMeetingRecorder `
    --hidden-import transcriber_worker `
    --hidden-import faster_whisper `
    --hidden-import faster_whisper.audio `
    --collect-data faster_whisper `
    --collect-data soundcard `
    --hidden-import huggingface_hub `
    --hidden-import huggingface_hub.hf_api `
    --hidden-import pycaw `
    --hidden-import pycaw.pycaw `
    --hidden-import pycaw.constants `
    --hidden-import comtypes `
    --hidden-import psutil `
    --exclude-module torch `
    --exclude-module tensorflow `
    --exclude-module matplotlib `
    --exclude-module pandas `
    --exclude-module scipy `
    --exclude-module sklearn `
    main.py
if ($LASTEXITCODE -ne 0) { throw "Executable build failed" }

$DistLlm = "dist\LocalMeetingRecorder\llm"
if (Test-Path "llm") {
    if (Test-Path $DistLlm) {
        Remove-Item $DistLlm -Recurse -Force
    }
    Copy-Item "llm" $DistLlm -Recurse
}

Write-Host "Build complete: dist\LocalMeetingRecorder\LocalMeetingRecorder.exe"
