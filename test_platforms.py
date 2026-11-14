"""Platform selection and capture-process lifecycle checks; no audio permissions needed."""
import os
import shlex
from pathlib import Path
import sys
import tempfile
import threading
import queue
from types import SimpleNamespace
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


class DeviceBufferTests(unittest.TestCase):
    def test_coreaudio_uses_device_default_instead_of_software_chunk_size(self):
        with patch.object(platform.sys, "platform", "darwin"):
            for requested in (9600, 4800, 256):
                self.assertIsNone(platform.audio_device_blocksize(requested))
        with patch.object(platform.sys, "platform", "win32"):
            self.assertEqual(platform.audio_device_blocksize(9600), 9600)

    def test_mac_capture_assembles_half_second_from_device_default_buffer(self):
        import main
        import numpy as np
        app = main.MeetingRecorderApp.__new__(main.MeetingRecorderApp)
        session = SimpleNamespace(stop_event=threading.Event(), active_event=threading.Event())
        session.active_event.set()
        source = Mock()
        recorder = Mock()
        reads = []

        def open_recorder(**kwargs):
            # Reproduce the reported CoreAudio constraint. The software read
            # can exceed this limit; it must not be sent to the hardware.
            size = kwargs["blocksize"]
            if size is not None and not 15 <= size <= 512:
                raise TypeError("blocksize must be between 15.0 and 512")
            context = Mock()
            context.__enter__ = Mock(return_value=recorder)
            context.__exit__ = Mock(return_value=False)
            return context

        def read(numframes):
            reads.append(numframes)
            if len(reads) == 10:
                session.stop_event.set()
            return np.full((numframes, 1), .25, dtype=np.float32)

        source.recorder.side_effect = open_recorder
        recorder.record.side_effect = read
        output, errors = queue.Queue(), queue.Queue()
        with patch.object(platform.sys, "platform", "darwin"):
            app.audio_capture_worker(session, "source_microphone", source,
                                     output, errors, .5, .05, .2)
        self.assertTrue(errors.empty())
        self.assertEqual(reads, [2400] * 10)
        audio = output.get_nowait()
        self.assertEqual(audio.shape, (24000,))
        np.testing.assert_array_equal(audio, np.full(24000, .25, dtype=np.float32))
        self.assertIsNone(output.get_nowait())
        self.assertTrue(output.empty())
        source.recorder.assert_called_once_with(samplerate=48000, channels=1, blocksize=None)

    def test_mac_chunk_playback_uses_device_default_but_keeps_software_blocks(self):
        import main
        import numpy as np
        player = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=player)
        context.__exit__ = Mock(return_value=False)
        soundcard = Mock()
        soundcard.default_speaker.return_value.player.return_value = context
        window = main.OfflineTranscriptWindow.__new__(main.OfflineTranscriptWindow)
        window.stop_event = threading.Event()
        window.active_event = threading.Event()
        window.active_event.set()
        with patch.dict(sys.modules, soundcard=soundcard), \
                patch.object(platform.sys, "platform", "darwin"):
            window.play_chunk_audio(np.zeros(24000, dtype=np.float32), 48000)
        soundcard.default_speaker.return_value.player.assert_called_once_with(
            samplerate=48000, channels=1, blocksize=None)
        self.assertEqual(player.play.call_count, 5)
        self.assertEqual(sum(call.args[0].size for call in player.play.call_args_list), 24000)


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


class ScreenshotPermissionTests(unittest.TestCase):
    def test_denied_permission_is_reported_before_running_screencapture(self):
        from atr_platform import macos
        quartz = Mock()
        quartz.CGPreflightScreenCaptureAccess.return_value = False
        with patch.dict(sys.modules, Quartz=quartz), patch('PIL.ImageGrab.grab') as grab:
            with self.assertRaisesRegex(PermissionError, 'saved code signature may be stale'):
                macos.capture_screenshot()
        grab.assert_not_called()

    def test_screencapture_failure_explains_how_to_rebind_current_app(self):
        import subprocess
        from atr_platform import macos
        quartz = Mock()
        quartz.CGPreflightScreenCaptureAccess.return_value = True
        error = subprocess.CalledProcessError(1, ['screencapture', '-x', '/tmp/test.png'])
        with patch.dict(sys.modules, Quartz=quartz), patch('PIL.ImageGrab.grab', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'add this copy with the plus button') as caught:
                macos.capture_screenshot()
        self.assertIs(caught.exception.__cause__, error)

    def test_shared_screenshot_pipeline_keeps_rgb_output(self):
        import main
        from PIL import Image
        app = main.MeetingRecorderApp.__new__(main.MeetingRecorderApp)
        with patch('main.capture_screenshot', return_value=Image.new('RGBA', (3, 2))):
            image = app.capture_screen_image()
        self.assertEqual(image.mode, 'RGB')
        self.assertEqual(image.size, (3, 2))


if __name__ == "__main__":
    unittest.main()
