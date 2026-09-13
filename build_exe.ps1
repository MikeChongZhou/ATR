$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name LocalMeetingRecorder --hidden-import transcriber_worker --hidden-import pycaw --hidden-import pycaw.pycaw --hidden-import pycaw.constants --hidden-import comtypes --hidden-import psutil main.py

Write-Host "Build complete: dist\LocalMeetingRecorder.exe"
