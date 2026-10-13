#!/usr/bin/env python3
"""Build the pinned tools on macOS. Requires Python 3.12+ and Xcode command line tools."""
import hashlib
import pathlib
import urllib.request

project_root = pathlib.Path(__file__).resolve().parents[1]
root = project_root / ".build" / "tool-sources"
root.mkdir(parents=True, exist_ok=True)
sources = [
    ("fio-3.43.tar.gz", "https://github.com/axboe/fio/archive/refs/tags/fio-3.43.tar.gz", "efa49b3f36eda9adf29294f27a87d7e457747d676cd0f73996d6365357832cfe"),
    ("smartmontools-7.5.tar.gz", "https://downloads.sourceforge.net/project/smartmontools/smartmontools/7.5/smartmontools-7.5.tar.gz", "690b83ca331378da9ea0d9d61008c4b22dde391387b9bbad7f29387f2595f76e"),
]
for name, url, digest in sources:
    path = root / name
    included = project_root / "third-party" / name
    if not path.exists() and included.exists():
        import shutil
        shutil.copy2(included, path)
    if not path.exists():
        print("Downloading " + name, flush=True)
        request = urllib.request.Request(url, headers={"User-Agent": "SSD-Lab-build/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            path.write_bytes(response.read())
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        raise RuntimeError("Checksum mismatch: " + name)
    print(name + " verified SHA256 " + actual, flush=True)

import pathlib
import os
import shutil
import subprocess
import tarfile

root = project_root / ".build"
destination = project_root / "tools"
destination.mkdir(parents=True, exist_ok=True)
source_delivery = project_root / "third-party"
source_delivery.mkdir(exist_ok=True)
for archive in (root / "tool-sources").glob("*.tar.gz"):
    with tarfile.open(archive) as package:
        package.extractall(root / "tool-sources", filter="data")
    shutil.copy2(archive, source_delivery / archive.name)

projects = [
    ("fio-fio-3.43", ["./configure", "--disable-native", "--disable-shm"], "fio", "fio", "COPYING"),
    ("smartmontools-7.5", ["./configure"], "smartctl", "smartctl", "COPYING"),
]
for name, configure, target, binary, license_name in projects:
    project = root / "tool-sources" / name
    print("Building " + name, flush=True)
    with (root / (name + ".log")).open("w") as logfile:
        environment = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": "13.0"}
        subprocess.run(["make", "clean"], cwd=project, stdout=logfile, stderr=subprocess.STDOUT, check=False)
        subprocess.run(configure, cwd=project, env=environment, stdout=logfile, stderr=subprocess.STDOUT, check=True)
        subprocess.run(["make", "-j4", target], cwd=project, env=environment, stdout=logfile, stderr=subprocess.STDOUT, check=True)
    shutil.copy2(project / binary, destination / binary)
    shutil.copy2(project / license_name, source_delivery / (binary + "-COPYING.txt"))
    print("Built " + str(destination / binary), flush=True)
