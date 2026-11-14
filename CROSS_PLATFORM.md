# Windows / macOS shared implementation

ATR uses one Python application for its windows, recording pipeline, MP3 encoding,
Whisper workers, settings, and durable summary queue. OS-specific audio discovery,
tray startup, data paths and power notifications live in `atr_platform/`.

## Run from source

Use Python 3.11 with Tkinter. On macOS with Homebrew:

```sh
brew install python@3.11 python-tk@3.11
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python packaging/build_macos.py --helper-only
python main.py
```

Compiling the native helper requires Xcode or Xcode Command Line Tools. System
capture requires macOS 13+. Dependencies may require a newer OS; the currently
validated build environment is Apple Silicon on macOS 26.5. Intel and older macOS
versions need their own builds and validation.

On Windows, existing commands remain valid:

```powershell
python -m pip install -r requirements.txt
python main.py
.\build_exe.ps1
```

## Build a Mac app

```sh
python -m pip install pyinstaller
python packaging/build_macos.py
```

Result: `dist/LocalMeetingRecorder.app`. The helper and app are signed ad hoc for
local testing. This is not a notarized public release. Build on each target OS and
CPU architecture; a Windows EXE cannot be reused on a Mac. The GitHub Actions
workflow runs tests and builds separate packages when pushed.

## Platform behavior

- Windows keeps its existing SoundCard loopback and pycaw activity detection.
- macOS system audio comes from `native/macos/SystemAudio.swift`, using
  ScreenCaptureKit without a virtual audio driver. The helper exports 48000 Hz
  mono float32 PCM through stdout. The Python adapter handles fragmented reads,
  silence, failure diagnostics, stop cancellation and process cleanup.
- Microphone audio uses SoundCard/CoreAudio on Mac. The recording dialog offers
  `auto`, `on`, `off`. Windows defaults to `auto`; Mac defaults to `on`, and Mac
  `auto` also means on because other-app microphone detection is not implemented.
- Windows microphone activity remains available for summary pauses and meeting
  reminders. On Mac these use silence and resource monitoring; no other-app
  microphone occupancy is inferred.
- macOS stores settings, recordings, screenshots, models and logs in
  `~/Library/Application Support/ATR`. Windows retains the existing portable
  program-directory layout. `ATR_DATA_DIR` overrides the data directory on either
  platform for tests or a custom installation. Existing Windows settings are not
  automatically imported into the Mac data directory.
- macOS uses detached tray integration with Tk's native event loop, native wheel
  deltas and context menus, plus workspace sleep/power-off notifications. These
  notifications provide best-effort saving; abrupt power loss still needs the
  existing `.partial` recovery mechanism.
- Mac uses the existing faster-whisper CPU backend. No Metal transcription
  acceleration is claimed by this change.

## Permissions and local summaries

On first use, allow Microphone and Screen & System Audio Recording in macOS
System Settings. The app captures system audio; optional screenshots still use
Pillow. Permission denials appear as recording errors. Restart after granting
screen capture permission. Source runs may attribute permissions to Python or
Terminal; packaged-app permissions must be validated independently.

For managed Mac summaries, install `llama.cpp` (`brew install llama.cpp`) or set an
executable path to a compatible Mac `llama-server`. Standard Homebrew locations
are searched even when launched from Finder. Mac runtime auto-download is not
implemented. Alternatively disable managed server and point the existing
OpenAI-compatible URL at a running local service. Model downloads remain shared.

## Validation

```sh
python -m unittest discover -v
```

Automated tests cover shared behavior and platform selection, microphone modes,
Mac capture pipe framing, permission-error reporting and child-process cleanup.
Synthetic audio helper tests do not require recording permissions.

Before treating a package as release-ready, manually verify on both OSes:

1. Launch, tray menu, close-to-tray, exit, bilingual UI and model downloads.
2. System audio plus microphone, mic off, wired/Bluetooth headphones and device changes.
3. Chinese/English real-time and offline transcription; MP3/TXT output and summary recovery.
4. First-launch permission prompts, permission refusal, sleep and shutdown during recording.
5. An hour-long recording for audio drift, buffering and resource usage.

The initial platform split preserves the shared application in `main.py`; further
extraction of recording and transcription orchestration can happen behind these
interfaces without duplicating the application.

### Initial validation record (2026-11-13)

On this Apple Silicon Mac (macOS 26.5, Python 3.11): all 45 tests passed; the Swift
helper compiled; the `.app` build completed; `codesign --verify --deep --strict`
passed. A source UI smoke test opened the main tab, registered the tray and power
hooks, and exited normally. A separate packaged-app check remained running through
startup without errors. Neither smoke test started audio recording.

Windows execution, real audio capture, first-launch recording permissions, long
recordings, and live Whisper/LLM model inference remain unverified. The workflow is
provided but has not been run on GitHub from this local branch.


### 1.0.8: macOS Settings crash

Native Cocoa tray callbacks now post Python-only queue entries; a Tk-owned timer
runs their UI actions. Calling `root.after` directly from Cocoa could re-enter Tcl
with a NULL Python thread state and abort the process. Power notifications use the
same dispatch path. Regression tests enter the real native menu through Cocoa
NSTimer callbacks and repeatedly select Settings, Offline, Recording, About and
Summary Queue before exiting. These run in a subprocess so a fatal abort fails the
test rather than taking down the suite.

The Mac build now records the application version in Info.plist and creates
`dist/ATR-macOS.zip` with `ditto`, preserving bundle symlinks and executable bits
when the package is uploaded as a GitHub Actions artifact.


### 1.0.9: CoreAudio device buffer compatibility

Mac recording and both offline playback paths now let SoundCard use the device's
native hardware buffer (`blocksize=None`). The software read and transcription
chunk sizes remain independent. Previously the capture worker requested 9600
hardware frames by default, which fails on AudioUnits limited to 512 frames.
Windows retains the existing explicit buffer sizes. A capture-worker regression
uses the reported 15–512 frame hardware limit and verifies that 2400-frame reads
still assemble a complete 24000-frame audio block without losing samples.
