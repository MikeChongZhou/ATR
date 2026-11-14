"""OS boundary. Shared recording/transcription code uses only these functions."""
from __future__ import annotations
import sys
import os
import threading
from pathlib import Path
from .interfaces import CaptureSource


def data_directory(source_directory: Path) -> Path:
    if os.environ.get("ATR_DATA_DIR"):
        return Path(os.environ["ATR_DATA_DIR"]).expanduser().resolve()
    if sys.platform == "darwin":
        directory = Path.home() / "Library" / "Application Support" / "ATR"
        return directory
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return source_directory


def audio_device_blocksize(requested_frames: int) -> int | None:
    """Keep CoreAudio's device buffer separate from software read/chunk sizes.

    SoundCard's macOS AudioUnit can reject large buffers (often over 512
    frames). None selects the device default; record(numframes=...) still
    accumulates the requested number of samples in software.
    """
    return None if sys.platform == "darwin" else requested_frames


def default_microphone_mode() -> str:
    return "on" if sys.platform == "darwin" else "auto"


def is_microphone_in_use_by_other_app() -> bool:
    if sys.platform == "win32":
        from .windows import is_microphone_in_use_by_other_app as detect
        return detect()
    # macOS has no equivalent implementation here. Do not infer occupancy
    # from opening our own input device or from the chosen recording mode.
    return False


def capture_sources(microphone_mode: str, stop_event) -> list[tuple[str, CaptureSource]]:
    if microphone_mode not in {"auto", "on", "off"}:
        raise ValueError(f"Unknown microphone mode: {microphone_mode}")
    if sys.platform == "win32":
        from .windows import capture_sources as discover
    elif sys.platform == "darwin":
        from .macos import capture_sources as discover
    else:
        raise RuntimeError(f"Recording is unsupported on {sys.platform}")
    return discover(microphone_mode, stop_event)


def start_tray(icon) -> None:
    if sys.platform == "darwin":
        icon.run_detached()
    else:
        threading.Thread(target=icon.run, name="tray", daemon=True).start()


def install_power_hooks(on_stop):
    """Return cleanup; Windows notifications remain attached to its HWND."""
    if sys.platform != "darwin":
        return lambda: None
    from AppKit import NSWorkspace, NSWorkspaceWillSleepNotification, NSWorkspaceWillPowerOffNotification
    center = NSWorkspace.sharedWorkspace().notificationCenter()
    tokens = []
    for name, reason in ((NSWorkspaceWillSleepNotification, "macos_sleep"),
                         (NSWorkspaceWillPowerOffNotification, "macos_shutdown")):
        tokens.append(center.addObserverForName_object_queue_usingBlock_(
            name, None, None, lambda notification, reason=reason: on_stop(reason)))

    def cleanup():
        for token in tokens:
            center.removeObserver_(token)
    return cleanup


def capture_screenshot():
    if sys.platform == "darwin":
        from .macos import capture_screenshot as capture
        return capture()
    from PIL import ImageGrab
    try:
        return ImageGrab.grab(all_screens=True)
    except TypeError:
        return ImageGrab.grab()
