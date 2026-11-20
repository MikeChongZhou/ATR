"""Build on macOS: python packaging/build_macos.py [--helper-only]."""
from pathlib import Path
import argparse
import os
import plistlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",
               b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def sign_bundle(bundle, identity):
    # Sign local builds without requiring Apple's timestamp service. This is a
    # stable certificate identity for TCC, not a claim of notarized distribution.
    if identity != "-":
        for path in sorted(bundle.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                is_macho = stream.read(4) in MACHO_MAGIC
            if is_macho:
                subprocess.run(["codesign", "--force", "--sign", identity, "--timestamp=none", str(path)], check=True)
    subprocess.run(["codesign", "--force", "--deep", "--sign", identity,
                    "--timestamp=none", str(bundle)], check=True)



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper-only", action="store_true")
    parser.add_argument("--sign-identity", default=os.environ.get("ATR_MAC_SIGN_IDENTITY", "-"),
                        help="Developer ID or Apple Development identity; '-' creates an ad-hoc test build")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Build the macOS app on macOS.")
    output_dir = args.output_dir.resolve()
    helper = ROOT / "native/macos/atr-system-audio"
    cache = ROOT / "build/swift-module-cache"
    cache.mkdir(parents=True, exist_ok=True)
    subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-O",
                    "-module-cache-path", str(cache),
                    "-target", os.uname().machine + "-apple-macosx13.0",
                    str(ROOT / "native/macos/SystemAudio.swift"), "-o", str(helper)], check=True)
    subprocess.run(["codesign", "--force", "--sign", args.sign_identity,
                    "--identifier", "com.mikechongzhou.atr.capture", "--timestamp=none", str(helper)], check=True)
    if args.helper_only:
        return
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir",
                    "--windowed", "--name", "LocalMeetingRecorder",
                    "--distpath", str(output_dir),
                    "--osx-bundle-identifier", "com.mikechongzhou.atr",
                    "--add-binary", str(helper) + ":native/macos",
                    "--collect-all", "faster_whisper", "--collect-all", "sherpa_onnx", "--collect-data", "soundcard",
                    "--hidden-import", "atr_platform.macos",
                    "--hidden-import", "psutil", str(ROOT / "main.py")], cwd=ROOT, check=True,
                   env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(ROOT / "build/pyinstaller-cache")})
    plist = output_dir / "LocalMeetingRecorder.app/Contents/Info.plist"
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
    sign_bundle(output_dir / "LocalMeetingRecorder.app", args.sign_identity)
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                    str(output_dir / "LocalMeetingRecorder.app"),
                    str(output_dir / "ATR-macOS.zip")], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict",
                    str(output_dir / "LocalMeetingRecorder.app")], check=True)
    print(f"Built {output_dir / 'LocalMeetingRecorder.app'} and {output_dir / 'ATR-macOS.zip'}")
    print("Signing identity:", args.sign_identity)


if __name__ == "__main__":
    main()
