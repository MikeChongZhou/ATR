"""Find or provision the managed llama.cpp runtime (models are separate)."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import tempfile
import threading
import urllib.request
import zipfile
import tarfile
from pathlib import Path, PurePosixPath
from typing import Callable

RELEASES_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
MAX_DOWNLOAD = 512 * 1024 * 1024
_install_lock = threading.Lock()


class SetupCancelled(Exception):
    pass


def find_runtime(directory: Path, configured: str = "") -> str | None:
    if configured.strip():
        candidate = Path(configured.strip()).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        found = shutil.which(configured.strip())
        if found:
            return found
        raise RuntimeError(f"Configured llama executable was not found: {configured}. Clear the executable setting to download it automatically.")
    for name in ("llama-server", "llama", "llama-server.exe", "llama.exe"):
        for parent in (directory / "llm", directory):
            candidate = parent / name
            if candidate.is_file():
                return str(candidate.resolve())
    local_servers = list((directory / "llm").rglob("llama-server")) if (directory / "llm").exists() else []
    if len(local_servers) == 1 and os.access(local_servers[0], os.X_OK):
        return str(local_servers[0].resolve())
    # Finder-launched apps often have a minimal PATH.
    if platform.system() == "Darwin":
        for prefix in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            candidate = prefix / "llama-server"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return shutil.which("llama-server") or shutil.which("llama")


def _open(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        "User-Agent": "LocalMeetingRecorder-runtime-setup",
        "Accept": "application/vnd.github+json" if url.startswith("https://api.github.com/") else "application/octet-stream",
    }), timeout=30)


def select_asset(releases: list, machine: str, system: str = "Windows") -> dict:
    architecture = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine.lower())
    if not architecture:
        raise RuntimeError(f"Automatic llama setup does not support this Windows architecture: {machine}")
    pattern = (rf"llama-[\w.-]+-bin-macos-{architecture}\.(?:tar\.gz|zip)" if system == "Darwin" else rf"llama-[\w.-]+-bin-win-cpu-{architecture}\.zip")
    for release in releases:
        if release.get("draft"):
            continue
        for asset in release.get("assets", []):
            if re.fullmatch(pattern, asset.get("name", "")):
                url = asset.get("browser_download_url", "")
                if not url.startswith("https://github.com/ggml-org/llama.cpp/releases/download/"):
                    raise RuntimeError("The llama release download URL is not from the official repository.")
                if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", asset.get("digest") or ""):
                    raise RuntimeError("The official llama release has no SHA256 checksum; automatic installation stopped.")
                return asset
    raise RuntimeError("No compatible llama runtime is available in the recent official releases. Try again later or configure a server executable.")


def ensure_runtime(directory: Path, configured: str = "", progress: Callable[[str], None] = lambda message: None,
                   cancelled: Callable[[], bool] = lambda: False) -> str:
    """Install only verified official binaries, with the server EXE published last."""
    existing = find_runtime(directory, configured)
    if existing:
        return existing
    if platform.system() not in ("Windows", "Darwin"):
        raise RuntimeError("Automatic llama setup supports Windows and macOS. Configure an existing server executable.")

    def check_cancelled():
        if cancelled():
            raise SetupCancelled()

    with _install_lock:
        existing = find_runtime(directory, configured)
        if existing:
            return existing
        check_cancelled()
        progress("正在查找本地模型服务 / Finding official llama server")
        try:
            with _open(RELEASES_URL) as response:
                metadata = response.read(8 * 1024 * 1024 + 1)
            if len(metadata) > 8 * 1024 * 1024:
                raise RuntimeError("The llama release metadata is unexpectedly large.")
            asset = select_asset(json.loads(metadata), platform.machine(), platform.system())
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".llama-setup-", dir=directory) as scratch:
                staging = Path(scratch)
                archive = staging / "runtime.zip"
                digest = hashlib.sha256()
                received = 0
                with _open(asset["browser_download_url"]) as response, archive.open("wb") as output:
                    total = int(response.headers.get("Content-Length", "0"))
                    if total > MAX_DOWNLOAD:
                        raise RuntimeError("The llama runtime download exceeds the size limit.")
                    while True:
                        check_cancelled()
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        received += len(block)
                        if received > MAX_DOWNLOAD:
                            raise RuntimeError("The llama runtime download exceeds the size limit.")
                        output.write(block)
                        digest.update(block)
                        amount = f"{received * 100 // total}%" if total else f"{received // (1024 * 1024)} MB"
                        progress(f"正在下载本地模型服务 / Downloading llama server: {amount}")
                progress("正在校验本地模型服务 / Verifying llama server SHA256")
                if digest.hexdigest() != asset["digest"].split(":", 1)[1].lower():
                    raise RuntimeError("The llama runtime checksum did not match. Download was discarded; click Retry to download again.")
                check_cancelled()
                unpacked = staging / "unpacked"
                mac = platform.system() == "Darwin"
                if asset["name"].endswith(".tar.gz"):
                    with tarfile.open(archive, "r:gz") as bundle:
                        if sum(m.size for m in bundle.getmembers()) > 1024 * 1024 * 1024:
                            raise RuntimeError("The llama runtime archive is unexpectedly large.")
                        bundle.extractall(unpacked, filter="data")
                else:
                    with zipfile.ZipFile(archive) as bundle:
                        entries = bundle.infolist()
                        if sum(entry.file_size for entry in entries) > 1024 * 1024 * 1024:
                            raise RuntimeError("The llama runtime archive is unexpectedly large.")
                        for entry in entries:
                            name = PurePosixPath(entry.filename.replace("\\", "/"))
                            if name.is_absolute() or ".." in name.parts or ":" in str(name):
                                raise RuntimeError("The llama archive contains an unsafe file path.")
                            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                                raise RuntimeError("The llama archive contains an unsupported symbolic link.")
                        progress("正在安装本地模型服务 / Installing llama server")
                        bundle.extractall(unpacked)
                server_name = "llama-server" if mac else "llama-server.exe"
                servers = list(unpacked.rglob(server_name))
                if len(servers) != 1:
                    raise RuntimeError("The downloaded archive does not contain a unique llama-server executable.")
                package = unpacked if mac else servers[0].parent
                server_relative = servers[0].relative_to(package)
                files = [p for p in package.rglob("*") if p.is_file()]
                if not mac and not (package / "llama.dll").is_file():
                    raise RuntimeError("The downloaded llama archive is missing llama.dll.")
                if any(not p.resolve().is_relative_to(unpacked.resolve()) for p in files):
                    raise RuntimeError("The runtime archive contains an unsafe link.")
                check_cancelled()
                target = directory / "llm"
                target.mkdir(exist_ok=True)
                # Publish entry points last so interrupted setup is retried next time.
                files.sort(key=lambda p: p.name.lower() in {"llama.exe", "llama-server.exe", "llama-server"})
                for source in files:
                    destination = target / source.relative_to(package)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, destination)
                progress("本地模型服务已就绪 / Llama server installed")
                server = target / server_relative
                if mac: server.chmod(server.stat().st_mode | 0o755)
                return str(server.resolve())
        except SetupCancelled:
            raise
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
            raise RuntimeError(f"Could not install the llama server: {exc}. Check your internet connection and folder permissions, then click Retry.") from exc
