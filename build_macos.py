#!/usr/bin/env python3
"""Build the native app. Pass --python-runtime to include a relocatable CPython."""
import argparse
import os
import pathlib
import plistlib
import shutil
import subprocess
import tempfile
import zipfile

root = pathlib.Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--python-runtime", type=pathlib.Path)
args = parser.parse_args()
final_application = root.parent / "SSD Lab.app"
staging = pathlib.Path(tempfile.mkdtemp(prefix="ssd-lab-macos-build-"))
application = staging / "SSD Lab.app"
contents = application / "Contents"
resources = contents / "Resources"
macos = contents / "MacOS"
macos.mkdir(parents=True, exist_ok=True)
resources.mkdir(exist_ok=True)
destination = resources / "SSD-Lab"
if destination.exists():
    shutil.rmtree(destination)
shutil.copytree(root, destination, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", "*.app", ".git", ".build", "dist", "screenshots"))
if args.python_runtime:
    runtime = resources / "python"
    if runtime.exists():
        shutil.rmtree(runtime)
    (runtime / "bin").mkdir(parents=True)
    source = args.python_runtime.resolve()
    version = subprocess.check_output([str(source / "bin/python3"), "-c", "import sys; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor))"], text=True).strip()
    shutil.copy2(source / "bin/python3", runtime / "bin/python3", follow_symlinks=True)
    (runtime / "lib").mkdir()
    shutil.copytree(source / "lib" / ("python" + version), runtime / "lib" / ("python" + version),
                    ignore=shutil.ignore_patterns("site-packages", "test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip", "__pycache__", "*.pyc"))
    # Only standard library is included; the backend has no pip dependencies.
    environment = {**os.environ, "PYTHONHOME": str(runtime), "PYTHONPATH": ""}
    subprocess.run([str(runtime / "bin/python3"), "-B", "-c", "import http.server, json, threading, subprocess, pathlib, plistlib; print('Bundled Python OK')"], env=environment, check=True)
plist = {
    "CFBundleName": "SSD Lab", "CFBundleDisplayName": "SSD Lab", "CFBundleExecutable": "SSD Lab",
    "CFBundleIdentifier": "local.ssdlab.desktop", "CFBundleVersion": "4", "CFBundleShortVersionString": "1.3.0",
    "CFBundlePackageType": "APPL", "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True,
    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    "NSAppleEventsUsageDescription": "SSD Lab uses the system folder picker to select a test folder.",
    "CFBundleDevelopmentRegion": "en", "CFBundleLocalizations": ["en", "zh-Hans", "zh-Hant", "ja", "ko", "de", "fr", "es"],
}
if (root / "native" / "AppIcon.icns").exists():
    shutil.copy2(root / "native" / "AppIcon.icns", resources / "AppIcon.icns")
    plist["CFBundleIconFile"] = "AppIcon"
(contents / "Info.plist").write_bytes(plistlib.dumps(plist))
subprocess.run(["swiftc", "-swift-version", "5", "-O", "-target", "arm64-apple-macosx13.0", "-module-cache-path", str(staging / "swift-cache"),
                str(root / "native" / "main.swift"), "-o", str(macos / "SSD Lab"), "-framework", "AppKit", "-framework", "WebKit"], check=True)
for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
    subprocess.run(["xattr", "-dr", attribute, str(application)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    subprocess.run(["xattr", "-d", attribute, str(application)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(application)], check=True)
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(application)], check=True)
archive = root.parent / "SSD-Lab-macOS-arm64.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as package:
    for path in application.rglob("*"):
        if path.is_file():
            package.write(path, "SSD Lab.app/" + str(path.relative_to(application)))
    if (root / "README.md").exists():
        package.write(root / "README.md", "README.md")
    if (root / "README.zh-CN.md").exists():
        package.write(root / "README.zh-CN.md", "README.zh-CN.md")
if final_application.exists():
    shutil.rmtree(final_application)
shutil.move(str(application), final_application)
shutil.rmtree(staging)
print(final_application)
print(archive)
