$ErrorActionPreference = "Stop"

$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python -m pip install -r requirements.txt
& $Python -m pip install pyinstaller
& $Python -m PyInstaller `
    --noconfirm `
    --onedir `
    --windowed `
    --name LocalMeetingRecorder `
    --hidden-import transcriber_worker `
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

Write-Host "Build complete: dist\LocalMeetingRecorder\LocalMeetingRecorder.exe"
