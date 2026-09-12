$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name LocalMeetingRecorder --hidden-import transcriber_worker main.py

Write-Host "Build complete: dist\LocalMeetingRecorder.exe"
