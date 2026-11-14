"""Build on macOS: python packaging/build_macos.py [--helper-only]."""
from pathlib import Path
import argparse
import os
import plistlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper-only", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Build the macOS app on macOS.")
    helper = ROOT / "native/macos/atr-system-audio"
    cache = ROOT / "build/swift-module-cache"
    cache.mkdir(parents=True, exist_ok=True)
    subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-O",
                    "-module-cache-path", str(cache),
                    "-target", os.uname().machine + "-apple-macosx13.0",
                    str(ROOT / "native/macos/SystemAudio.swift"), "-o", str(helper)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(helper)], check=True)
    if args.helper_only:
        return
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
                    "--windowed", "--name", "LocalMeetingRecorder",
                    "--osx-bundle-identifier", "com.mikechongzhou.atr",
                    "--add-binary", str(helper) + ":native/macos",
                    "--collect-all", "faster_whisper", "--collect-data", "soundcard",
                    "--hidden-import", "atr_platform.macos",
                    "--hidden-import", "psutil", str(ROOT / "main.py")], cwd=ROOT, check=True,
                   env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(ROOT / "build/pyinstaller-cache")})
    plist = ROOT / "dist/LocalMeetingRecorder.app/Contents/Info.plist"
    with plist.open("rb") as stream:
        info = plistlib.load(stream)
    version = re.search(r'^APP_VERSION = "([^"]+)"', (ROOT / "main.py").read_text(), re.MULTILINE).group(1)
    info.update(CFBundleShortVersionString=version, CFBundleVersion=version,
                NSMicrophoneUsageDescription="ATR records your microphone for local meeting transcripts.",
                NSScreenCaptureUsageDescription="ATR captures system audio and optional meeting screenshots.",
                LSMinimumSystemVersion="13.0")
    with plist.open("wb") as stream:
        plistlib.dump(info, stream)
    # Updating Info.plist invalidates the generated signature.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-",
                    str(ROOT / "dist/LocalMeetingRecorder.app")], check=True)
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                    str(ROOT / "dist/LocalMeetingRecorder.app"),
                    str(ROOT / "dist/ATR-macOS.zip")], check=True)
    print("Built dist/LocalMeetingRecorder.app and dist/ATR-macOS.zip (local ad-hoc signature).")


if __name__ == "__main__":
    main()
