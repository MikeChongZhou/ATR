"""ScreenCaptureKit system audio; SoundCard/CoreAudio microphone input."""
from __future__ import annotations
import contextlib
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time


def helper_path() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    candidate = root / "native" / "macos" / "atr-system-audio"
    if not candidate.is_file():
        raise RuntimeError("The macOS audio helper is missing. Run: python packaging/build_macos.py --helper-only")
    return candidate


class SystemAudioSource:
    def __init__(self, stop_event):
        self.stop_event = stop_event

    def recorder(self, *, samplerate, channels, blocksize):
        if samplerate != 48000 or channels != 1:
            raise ValueError("The macOS helper requires 48000 Hz mono audio")
        return SystemAudioRecorder(helper_path(), self.stop_event)


class SystemAudioRecorder:
    def __init__(self, executable, stop_event):
        self.executable = executable
        self.stop_event = stop_event
        self.process = None
        self.errors = None
        self.pending = bytearray()

    def __enter__(self):
        self.errors = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen([str(self.executable)], stdout=subprocess.PIPE,
                                            stderr=self.errors, bufsize=0)
            header = self._read(4, timeout=30)
            if header != b"ATR1":
                raise RuntimeError("System audio capture did not start. " + self.diagnostic())
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def diagnostic(self):
        self.errors.seek(0)
        return self.errors.read(4096).decode("utf-8", errors="replace").strip()

    def _read(self, count, timeout, allow_silence=False):
        deadline = time.monotonic() + timeout
        while len(self.pending) < count and not self.stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if allow_silence and self.process.poll() is None:
                    break
                raise RuntimeError("System audio capture timed out. Check Screen & System Audio Recording permission. "
                                   + self.diagnostic())
            ready, _, _ = select.select([self.process.stdout], [], [], min(0.1, remaining))
            if not ready:
                continue
            block = os.read(self.process.stdout.fileno(), max(4096, count - len(self.pending)))
            if not block:
                raise RuntimeError("System audio capture ended. " + self.diagnostic())
            self.pending.extend(block)
        available = min(count, len(self.pending))
        if allow_silence:
            available -= available % 4
        result = bytes(self.pending[:available])
        del self.pending[:available]
        if allow_silence and not self.stop_event.is_set():
            result += bytes(count - len(result))
        return result

    def record(self, numframes):
        import numpy as np
        data = self._read(numframes * 4, timeout=numframes / 48000, allow_silence=True)
        return np.frombuffer(data[:len(data) // 4 * 4], dtype="<f4").copy()

    def __exit__(self, *_args):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
            if self.process.stdout:
                self.process.stdout.close()
        if self.errors is not None:
            self.errors.close()


def capture_sources(microphone_mode, stop_event):
    # Validate before starting any source, so a missing helper cannot silently
    # turn a system-audio recording into a microphone-only recording.
    helper_path()
    sources = [("source_speaker", SystemAudioSource(stop_event))]
    # auto uses the Mac default (on); no reliable other-app detector is provided.
    if microphone_mode != "off":
        import soundcard as sc
        sources.append(("source_microphone", sc.default_microphone()))
    return sources


def capture_permission_message():
    executable = Path(sys.executable).resolve()
    app = next((parent for parent in executable.parents if parent.suffix == ".app"), executable)
    return (
        "macOS denied Screen & System Audio Recording for this copy of ATR. "
        "If the permission switch is already on after an update, its saved code signature may be stale. "
        "Quit ATR, remove the old LocalMeetingRecorder entry with the minus button, "
        "then add this copy with the plus button and enable it. Reopen ATR afterwards. "
        f"App: {app}"
    )


def capture_screenshot():
    from Quartz import CGPreflightScreenCaptureAccess
    from PIL import ImageGrab
    if not CGPreflightScreenCaptureAccess():
        raise PermissionError(capture_permission_message())
    try:
        return ImageGrab.grab()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("macOS could not capture a screenshot. " + capture_permission_message()) from exc
