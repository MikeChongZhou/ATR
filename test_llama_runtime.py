import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import llama_runtime as runtime


def archive_bytes(unsafe=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../outside.exe" if unsafe else "llama-server.exe", b"server")
        archive.writestr("llama.dll", b"dependency")
    return buffer.getvalue()


def release(data):
    return {"assets": [{
        "name": "llama-b123-bin-win-cpu-x64.zip",
        "browser_download_url": "https://github.com/ggml-org/llama.cpp/releases/download/b123/llama-b123-bin-win-cpu-x64.zip",
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
    }]}


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class RuntimeTests(unittest.TestCase):
    def provision(self, directory, data, metadata=None, progress=None, cancelled=lambda: False):
        responses = [Response(json.dumps(metadata or [{"assets": []}, release(data)]).encode()), Response(data)]
        with patch.object(runtime, "_open", side_effect=responses), \
             patch.object(runtime.platform, "system", return_value="Windows"), \
             patch.object(runtime.platform, "machine", return_value="AMD64"), \
             patch.object(runtime.shutil, "which", return_value=None):
            return runtime.ensure_runtime(directory, progress=progress or Mock(), cancelled=cancelled)

    def test_missing_runtime_downloads_verifies_installs_and_reports_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            messages = []
            executable = self.provision(path, archive_bytes(), progress=messages.append)
            self.assertEqual(Path(executable).read_bytes(), b"server")
            self.assertEqual((path / "llm" / "llama.dll").read_bytes(), b"dependency")
            self.assertTrue(any("100%" in message for message in messages))
            self.assertTrue(any("SHA256" in message for message in messages))
            self.assertFalse(list(path.glob(".llama-setup-*")))

    def test_existing_server_is_reused_without_network_and_preferred_over_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "llm").mkdir()
            for name in ("llama.exe", "llama-server.exe"):
                (path / "llm" / name).touch()
            with patch.object(runtime, "_open") as network:
                executable = runtime.ensure_runtime(path)
            self.assertEqual(Path(executable).name, "llama-server.exe")
            network.assert_not_called()

    def test_checksum_mismatch_does_not_install_and_retry_can_succeed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            data = archive_bytes()
            wrong = [release(data)]
            wrong[0]["assets"][0]["digest"] = "sha256:" + "0" * 64
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                self.provision(path, data, metadata=wrong)
            self.assertFalse((path / "llm").exists())
            self.assertTrue(Path(self.provision(path, data)).is_file())

    def test_archive_cannot_escape_install_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "unsafe file path"):
                self.provision(path, archive_bytes(unsafe=True))
            self.assertFalse((path / "llm").exists())
            self.assertFalse((path / "outside.exe").exists())

    def test_cancelled_download_leaves_no_runtime_or_staging_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with self.assertRaises(runtime.SetupCancelled):
                self.provision(path, archive_bytes(), cancelled=Mock(side_effect=[False, True]))
            self.assertEqual(list(path.iterdir()), [])

    def test_untrusted_or_unsigned_asset_is_rejected(self):
        asset = release(b"data")
        asset["assets"][0]["browser_download_url"] = "https://example.com/server.zip"
        with self.assertRaisesRegex(RuntimeError, "official repository"):
            runtime.select_asset([asset], "AMD64")
        asset = release(b"data")
        asset["assets"][0]["digest"] = None
        with self.assertRaisesRegex(RuntimeError, "SHA256"):
            runtime.select_asset([asset], "AMD64")

    def test_invalid_custom_path_is_reported_without_downloading(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime.shutil, "which", return_value=None), \
             patch.object(runtime, "_open") as network:
            with self.assertRaisesRegex(RuntimeError, "Configured llama executable"):
                runtime.ensure_runtime(Path(directory), "missing-custom-llama.exe")
            network.assert_not_called()


class ServerErrorsTests(unittest.TestCase):
    def app(self):
        import main
        import threading
        app = main.MeetingRecorderApp.__new__(main.MeetingRecorderApp)
        app.llm_health_ok = Mock(return_value=False)
        app.llm_process_lock = threading.Lock()
        app.llm_process = None
        app.shutting_down = False
        app.summary_should_pause = Mock(return_value=False)
        return app

    def test_missing_model_has_specific_error_and_does_not_download_runtime(self):
        import main
        app = self.app()
        with patch("main.ensure_runtime") as installer:
            with self.assertRaisesRegex(RuntimeError, "model file was not found"):
                app.ensure_managed_llm_server({"llm_server_model_path": "missing-model.gguf"})
            installer.assert_not_called()

    def test_early_server_exit_includes_log_in_error(self):
        app = self.app()
        app.llm_process = Mock()
        app.llm_process.poll.side_effect = [None, 7]
        app.llm_process.returncode = 7
        app.llm_server_diagnostic = Mock(return_value="failed to allocate memory")
        with self.assertRaisesRegex(RuntimeError, "code 7.*failed to allocate memory"):
            app.ensure_managed_llm_server({})

    def test_download_does_not_block_recording_process_lock_and_exit_cancels_start(self):
        app = self.app()

        def download(*args, **kwargs):
            self.assertTrue(app.llm_process_lock.acquire(blocking=False))
            app.llm_process_lock.release()
            app.shutting_down = True
            return "llama-server.exe"

        with patch("main.ensure_runtime", side_effect=download), patch("main.subprocess.Popen") as launch:
            self.assertFalse(app.ensure_managed_llm_server({"llm_server_model_ref": "test-model"}))
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
