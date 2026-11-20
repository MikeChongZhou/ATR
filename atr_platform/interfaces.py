"""Minimal recorder contract shared by SoundCard and native macOS capture."""
from __future__ import annotations
from typing import ContextManager, Protocol, Any


class Recorder(Protocol):
    def record(self, numframes: int) -> Any:
        """Return mono float32 samples at the requested sample rate."""
        ...


class CaptureSource(Protocol):
    def recorder(self, *, samplerate: int, channels: int, blocksize: int | None) -> ContextManager[Recorder]:
        ...
