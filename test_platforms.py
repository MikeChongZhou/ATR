"""Platform selection and capture-process lifecycle checks; no audio permissions needed."""
import os
import shlex
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import atr_platform as platform
from atr_platform import windows


class PlatformTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"ATR_DATA_DIR": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_mac_data_does_not_go_inside_bundle(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(platform.sys, "platform", "darwin"), \
                patch.object(platform.Path, "home", return_value=Path(directory)):
            result = platform.data_directory(Path("/Applications/ATR.app/Contents/MacOS"))
            self.assertEqual(result, Path(directory) / "Library/Application Support/ATR")
            self.assertFalse(result.exists())  # Resolving a path has no write side effects.

    def test_windows_source_directory_is_preserved(self):
        with patch.object(platform.sys, "platform", "win32"):
            self.assertEqual(platform.data_directory(Path("project")), Path("project"))
            self.assertEqual(platform.default_microphone_mode(), "auto")

    def test_mac_tray_uses_detached_loop(self):
        icon = Mock()
        with patch.object(platform.sys, "platform", "darwin"):
            platform.start_tray(icon)
        icon.run_detached.assert_called_once()
        icon.run.assert_not_called()

    def test_windows_manual_mic_does_not_depend_on_other_app(self):
        soundcard = Mock()
        with patch.dict(sys.modules, soundcard=soundcard), \
                patch.object(windows, "is_microphone_in_use_by_other_app", return_value=False):
            self.assertEqual(len(windows.capture_sources("on", None)), 2)
            self.assertEqual(len(windows.capture_sources("auto", None)), 1)
            self.assertEqual(len(windows.capture_sources("off", None)), 1)

    def test_invalid_mode_is_rejected_before_opening_devices(self):
        with self.assertRaises(ValueError):
            platform.capture_sources("invalid", threading.Event())

    def test_mac_off_mode_never_opens_microphone(self):
        from atr_platform import macos
        soundcard = Mock()
        with patch.dict(sys.modules, soundcard=soundcard), patch.object(macos, "helper_path"):
            self.assertEqual(len(macos.capture_sources("off", threading.Event())), 1)
        soundcard.default_microphone.assert_not_called()


@unittest.skipUnless(sys.platform == "darwin", "POSIX pipe capture backend")
class MacCaptureTests(unittest.TestCase):
    def helper(self, body):
        # Fake native helper with the exact binary pipe protocol.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "helper"
        path.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -c " + shlex.quote(body))
        path.chmod(0o755)
        return path

    def test_fragmented_header_and_pcm_are_reassembled_and_process_stops(self):
        from atr_platform.macos import SystemAudioRecorder
        helper = self.helper("import os, struct, time\nos.write(1,b'AT')\ntime.sleep(.02)\n"
                             "os.write(1,b'R1'+struct.pack('<ff',.25,-.5))\ntime.sleep(30)\n")
        with SystemAudioRecorder(helper, threading.Event()) as recorder:
            self.assertEqual(recorder.record(2).tolist(), [.25, -.5])
        self.assertIsNotNone(recorder.process.poll())

    def test_permission_error_survives_process_exit(self):
        from atr_platform.macos import SystemAudioRecorder
        helper = self.helper("import sys\nsys.stderr.write('Permission denied')\nsys.exit(1)\n")
        recorder = SystemAudioRecorder(helper, threading.Event())
        with self.assertRaisesRegex(RuntimeError, "Permission denied"):
            with recorder:
                pass
        self.assertIsNotNone(recorder.process.poll())

    def test_stop_interrupts_capture_startup(self):
        from atr_platform.macos import SystemAudioRecorder
        helper = self.helper("import time\ntime.sleep(30)\n")
        stop = threading.Event()
        timer = threading.Timer(.1, stop.set)
        timer.start()
        self.addCleanup(timer.join)
        recorder = SystemAudioRecorder(helper, stop)
        with self.assertRaises(RuntimeError):
            with recorder:
                pass
        self.assertIsNotNone(recorder.process.poll())

    def test_silent_system_returns_silence_without_stopping_recording(self):
        from atr_platform.macos import SystemAudioRecorder
        helper = self.helper("import os, time\nos.write(1,b'ATR1')\ntime.sleep(30)\n")
        with SystemAudioRecorder(helper, threading.Event()) as recorder:
            self.assertEqual(recorder.record(480).tolist(), [0.] * 480)
            self.assertIsNone(recorder.process.poll())


if __name__ == "__main__":
    unittest.main()
