"""Deliver native tray callbacks through Tk's own event loop.

On macOS Cocoa calls Python while Tcl has released its thread state. Calling
Tk (including root.after) in that callback can abort in PyEval_RestoreThread.
Posting is deliberately Python-only; only the Tk timer touches widgets.
"""
from __future__ import annotations
import queue
import sys


class UiDispatcher:
    def __init__(self, root, interval_ms=25):
        self.root = root
        self.interval_ms = interval_ms
        self.pending = queue.SimpleQueue()
        self.timer = None
        self.closed = False

    def post(self, callback, *args):
        if not self.closed:
            self.pending.put((callback, args))

    def start(self):
        if self.timer is None and not self.closed:
            self.timer = self.root.after(self.interval_ms, self._drain)

    def _drain(self):
        self.timer = None
        # Bound each batch so native menu bursts cannot starve Tk's events.
        for _ in range(64):
            if self.closed:
                return
            try:
                callback, args = self.pending.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args)
            except Exception:
                self.root.report_callback_exception(*sys.exc_info())
        self.start()

    def stop(self):
        self.closed = True
        if self.timer is not None:
            try:
                self.root.after_cancel(self.timer)
            except Exception:
                pass  # Tk may already have been destroyed by normal Exit.
            self.timer = None
