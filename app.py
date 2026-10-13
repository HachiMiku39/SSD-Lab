#!/usr/bin/env python3
"""SSD Lab: local-only fio benchmarks and read-only smartctl monitoring."""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import math
import os
import platform
import plistlib
import re
import secrets
import shlex
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from plot_logs import parse_logs, bound_series
from apple_nand import apple_nand_info

ROOT = Path(__file__).resolve().parent
VERSION = "1.3.1"
PROFILES = {
    "standard": [
        {"id": "seq8", "label": "SEQ1M", "detail": "Q8T1", "bs": "1M", "depth": 8, "threads": 1, "random": False},
        {"id": "seq1", "label": "SEQ1M", "detail": "Q1T1", "bs": "1M", "depth": 1, "threads": 1, "random": False},
        {"id": "rnd32", "label": "RND4K", "detail": "Q32T1", "bs": "4k", "depth": 32, "threads": 1, "random": True},
        {"id": "rnd1", "label": "RND4K", "detail": "Q1T1", "bs": "4k", "depth": 1, "threads": 1, "random": True},
    ],
    "nvme": [
        {"id": "seq8", "label": "SEQ1M", "detail": "Q8T1", "bs": "1M", "depth": 8, "threads": 1, "random": False},
        {"id": "seq32", "label": "SEQ128K", "detail": "Q32T1", "bs": "128k", "depth": 32, "threads": 1, "random": False},
        {"id": "rnd16", "label": "RND4K", "detail": "Q32T16", "bs": "4k", "depth": 32, "threads": 16, "random": True},
        {"id": "rnd1", "label": "RND4K", "detail": "Q1T1", "bs": "4k", "depth": 1, "threads": 1, "random": True},
    ],
    "qos": [
        {"id": "mix1", "label": "MIX4K", "detail": "R70/W30 Q1T1", "bs": "4k", "depth": 1, "threads": 1, "random": True, "mix_read": 70},
        {"id": "mix4", "label": "MIX4K", "detail": "R70/W30 Q4T1", "bs": "4k", "depth": 4, "threads": 1, "random": True, "mix_read": 70},
        {"id": "mix16", "label": "MIX4K", "detail": "R70/W30 Q16T1", "bs": "4k", "depth": 16, "threads": 1, "random": True, "mix_read": 70},
        {"id": "mix32", "label": "MIX4K", "detail": "R70/W30 Q32T1", "bs": "4k", "depth": 32, "threads": 1, "random": True, "mix_read": 70},
    ],
    "posix": [
        {"id": "seq1", "label": "SEQ1M", "detail": "Q1T1", "bs": "1M", "depth": 1, "threads": 1, "random": False},
        {"id": "seq128", "label": "SEQ128K", "detail": "Q1T1", "bs": "128k", "depth": 1, "threads": 1, "random": False},
        {"id": "rnd1", "label": "RND4K", "detail": "Q1T1", "bs": "4k", "depth": 1, "threads": 1, "random": True},
        {"id": "rnd4t", "label": "RND4K", "detail": "Q1T4", "bs": "4k", "depth": 1, "threads": 4, "random": True},
    ],
}


class AppError(Exception):
    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics


class Cancelled(Exception):
    pass


def command(args, timeout=15):
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AppError(str(exc)) from exc


def parse_json_output(output):
    # fio can emit informational lines before its final JSON object.
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", output):
        try:
            data, _ = decoder.raw_decode(output[match.start():])
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    raise AppError("The tool returned no valid JSON; check its version and device permissions.")


def locate_tool(name, explicit=""):
    candidates = [explicit] if explicit else [
        str(ROOT / "tools" / (name + (".exe" if os.name == "nt" else ""))),
        shutil.which(name), f"/opt/homebrew/bin/{name}", f"/opt/homebrew/sbin/{name}",
        f"/usr/local/bin/{name}", f"/usr/local/sbin/{name}", f"/usr/sbin/{name}",
    ]
    if os.name == "nt" and not explicit:
        candidates += [str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "fio" / "fio.exe"),
                       str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "smartmontools" / "bin" / "smartctl.exe")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return None


def dependency(name, explicit=""):
    path = locate_tool(name, explicit)
    result = {"name": name, "path": path, "available": False, "version": None, "error": None}
    if not path:
        result["error"] = "Tool not found"
        return result
    try:
        proc = command([path, "--version"], timeout=8)
        pattern = r"fio-(\d+\.\d+(?:[^\s]*)?)" if name == "fio" else r"smartctl\s+(\d+\.\d+)"
        match = re.search(pattern, proc.stdout + proc.stderr)
        if not match or proc.returncode:
            raise AppError("Not a valid fio / smartctl executable.")
        if name == "smartctl" and float(match.group(1)) < 7:
            raise AppError("smartmontools 7.0 or later is required for JSON support.")
        result.update(available=True, version=match.group(1))
    except AppError as exc:
        result["error"] = str(exc)
    return result


def disk_usage(path):
    try:
        usage = shutil.disk_usage(path)
        return {"total": usage.total, "free": usage.free, "used": usage.used}
    except OSError:
        return {"total": None, "free": None, "used": None}


def scan_volumes():
    volumes, warning = [], None
    try:
        system = platform.system()
        if system == "Darwin":
            proc = subprocess.run(["/usr/sbin/diskutil", "list", "-plist"], capture_output=True, timeout=12)
            if proc.returncode:
                raise AppError("Disk enumeration is unavailable; choose a test folder manually.")
            data = plistlib.loads(proc.stdout)
            def walk(node):
                if isinstance(node, dict):
                    mount = node.get("MountPoint")
                    if mount:
                        volumes.append({"name": node.get("VolumeName") or mount,
                                        "path": mount, "device": "/dev/" + node.get("DeviceIdentifier", ""), **disk_usage(mount)})
                    for value in node.values():
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)
            walk(data)
        elif system == "Linux":
            proc = command(["lsblk", "-J", "-o", "NAME,PATH,MOUNTPOINT,LABEL,TYPE"], 12)
            if proc.returncode:
                raise AppError("Unable to enumerate volumes; choose a test folder manually.")
            def walk(nodes):
                for node in nodes:
                    mount = node.get("mountpoint")
                    if mount:
                        volumes.append({"name": node.get("label") or mount, "path": mount,
                                        "device": node.get("path"), **disk_usage(mount)})
                    walk(node.get("children", []))
            walk(json.loads(proc.stdout).get("blockdevices", []))
        elif system == "Windows":
            script = "Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,VolumeName,Size,FreeSpace | ConvertTo-Json -Compress"
            proc = command(["powershell", "-NoProfile", "-Command", script], 12)
            data = json.loads(proc.stdout or "[]")
            for node in data if isinstance(data, list) else [data]:
                path = node["DeviceID"] + "\\"
                volumes.append({"name": node.get("VolumeName") or path, "path": path, "device": None, **disk_usage(path)})
    except (AppError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        warning = str(exc)
    if not volumes:
        paths = [Path.home().anchor]
        if platform.system() == "Darwin" and Path("/Volumes").is_dir():
            paths += [str(path) for path in Path("/Volumes").iterdir() if path.is_dir()]
        volumes = [{"name": "System volume" if index == 0 else Path(path).name, "path": path, "device": None, **disk_usage(path)}
                   for index, path in enumerate(paths) if path]
    return {"volumes": list({v["path"]: v for v in volumes}.values()), "warning": warning}


def smart_summary(raw, exit_code):
    nvme = raw.get("nvme_smart_health_information_log") or {}
    ata = (raw.get("ata_smart_attributes") or {}).get("table", [])
    messages = [item.get("string", "") for item in (raw.get("smartctl") or {}).get("messages", [])]
    passed = (raw.get("smart_status") or {}).get("passed")
    critical = nvme.get("critical_warning")
    has_health = passed is not None or critical is not None
    health = "unknown"
    if passed is False or exit_code & 8:
        health = "bad"
    elif has_health:
        health = "warning" if critical or exit_code & (16 | 32 | 64 | 128) else "good"
    used = nvme.get("percentage_used")
    # Remaining-life percentages for ATA devices are vendor-specific; never guess.
    remaining = max(0, min(100, 100 - used)) if isinstance(used, (int, float)) else None
    if remaining == 0 and health == "good":
        health = "warning"
    temperature = (raw.get("temperature") or {}).get("current", nvme.get("temperature"))
    power_hours = (raw.get("power_on_time") or {}).get("hours", nvme.get("power_on_hours"))
    power_cycles = raw.get("power_cycle_count", nvme.get("power_cycles"))
    media_errors = nvme.get("media_errors")
    attributes = []
    names = {
        "critical_warning": "Critical warning", "temperature": "Temperature", "available_spare": "Available spare",
        "available_spare_threshold": "Spare threshold", "percentage_used": "Percentage used",
        "data_units_read": "Data units read", "data_units_written": "Data units written",
        "host_reads": "Host read commands", "host_writes": "Host write commands", "controller_busy_time": "Controller busy time",
        "power_cycles": "Power cycles", "power_on_hours": "Power-on hours", "unsafe_shutdowns": "Unsafe shutdowns",
        "media_errors": "Media and data integrity errors", "num_err_log_entries": "Error log entries",
        "warning_temp_time": "Warning temperature time", "critical_comp_time": "Critical temperature time",
    }
    units = {"temperature": "°C", "available_spare": "%", "available_spare_threshold": "%", "percentage_used": "%",
             "controller_busy_time": "min", "power_on_hours": "h", "warning_temp_time": "min", "critical_comp_time": "min"}
    for key, value in nvme.items():
        if isinstance(value, (int, float, str)) and not key.endswith("_s"):
            attributes.append({"id": key, "name": names.get(key, key), "value": nvme.get(key + "_s", value),
                               "unit": units.get(key, ""), "threshold": None, "worst": None,
                               "status": "warning" if key == "critical_warning" and value else "normal"})
    for item in ata:
        attributes.append({"id": f'{item.get("id", 0):02X}', "name": item.get("name", "Unknown attribute"),
                           "value": item.get("value"), "worst": item.get("worst"), "threshold": item.get("thresh"),
                           "raw": (item.get("raw") or {}).get("string", (item.get("raw") or {}).get("value")),
                           "unit": "", "status": "warning" if item.get("when_failed") not in (None, "", "-") else "normal"})
    read_units, write_units = nvme.get("data_units_read"), nvme.get("data_units_written")
    partial = bool(exit_code & 7)
    if partial and not messages:
        messages.append("Some SMART commands failed; check permissions or USB bridge support.")
    if not has_health and not messages:
        messages.append("The device did not return SMART health data.")
    return {
        "model": raw.get("model_name") or raw.get("scsi_model_name") or raw.get("product") or "Unknown model",
        "serial": raw.get("serial_number"), "firmware": raw.get("firmware_version") or raw.get("revision"),
        "protocol": (raw.get("device") or {}).get("protocol"), "capacity": (raw.get("user_capacity") or {}).get("bytes"),
        "health": health, "remaining": remaining, "percentage_used": used, "temperature": temperature,
        "power_hours": power_hours, "power_cycles": power_cycles, "media_errors": media_errors,
        "read_bytes": read_units * 512000 if isinstance(read_units, (int, float)) else None,
        "write_bytes": write_units * 512000 if isinstance(write_units, (int, float)) else None,
        "attributes": attributes, "messages": messages, "partial": partial, "health_data_available": has_health,
        "exit_code": exit_code, "raw": raw, "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def smart_command(args, elevated=False):
    if type(elevated) is not bool:
        raise AppError("Invalid permission mode.")
    if elevated:
        if platform.system() != "Darwin":
            raise AppError("On this platform, run the app as administrator to read SMART.")
        # Only fixed read-only smartctl calls assembled by Lab are accepted.
        shell = shlex.join(args) + "; exit 0"
        script = "do shell script " + json.dumps(shell, ensure_ascii=False) + " with administrator privileges"
        proc = command(["/usr/bin/osascript", "-e", script], timeout=120)
        if proc.returncode:
            raise AppError("System authorization cancelled or SMART read failed: " + proc.stderr.strip())
        raw = parse_json_output(proc.stdout)
        code = (raw.get("smartctl") or {}).get("exit_status", 0)
        return raw, code
    proc = command(args, timeout=25)
    return parse_json_output(proc.stdout), proc.returncode


def fio_metrics(raw, direction):
    jobs = raw.get("jobs") or []
    if not jobs:
        raise AppError("fio returned no test jobs.")
    if any(job.get("error", 0) for job in jobs):
        raise AppError("fio job failed: " + ", ".join(str(job.get("error")) for job in jobs if job.get("error")))
    bandwidth = iops = weighted_latency = count = byte_count = 0
    for job in jobs:
        metric = job.get(direction) or {}
        bandwidth += metric.get("bw_bytes", metric.get("bw", 0) * 1024)
        iops += metric.get("iops", 0)
        operations = metric.get("total_ios", 0)
        byte_count += metric.get("io_bytes", metric.get("io_kbytes", 0) * 1024)
        if "lat_ns" in metric:
            latency = metric["lat_ns"].get("mean", 0) / 1000
        elif "lat_us" in metric:
            latency = metric["lat_us"].get("mean", 0)
        else:
            latency = (metric.get("clat_ns") or {}).get("mean", 0) / 1000
        weighted_latency += latency * operations
        count += operations
    if not count or bandwidth <= 0:
        raise AppError("fio produced no valid I/O; this run is not recorded as successful.")
    tails = {}
    # Percentiles cannot be averaged across jobs. group_reporting normally emits
    # one aggregated job; otherwise retain the raw data and leave these unknown.
    if len(jobs) == 1:
        latency_data = (jobs[0].get(direction) or {}).get("lat_ns") or {}
        percentiles = {float(key): value / 1000 for key, value in latency_data.get("percentile", {}).items()}
        for percentile, field in [(50, "p50_us"), (95, "p95_us"), (99, "p99_us"), (99.9, "p999_us"), (99.99, "p9999_us"), (99.999, "p99999_us")]:
            tails[field] = percentiles.get(percentile)
        tails["max_latency_us"] = latency_data["max"] / 1000 if "max" in latency_data and latency_data.get("N", count) else None
    else:
        tails = {field: None for field in ("p50_us", "p95_us", "p99_us", "p999_us", "p9999_us", "p99999_us", "max_latency_us")}
    return {"mb_s": bandwidth / 1_000_000, "iops": iops, "latency_us": weighted_latency / count,
            "io_bytes": byte_count, "total_ios": count, "depth_distribution": jobs[0].get("iodepth_level", {}),
            **tails, "percentile_kind": "total_latency", "tail_sample_warning": count < 100_000}


def sample_statistics(samples):
    values = [value["mb_s"] for value in samples]
    mean = statistics.mean(values)
    return {"mean_mb_s": mean, "median_mb_s": statistics.median(values), "min_mb_s": min(values), "max_mb_s": max(values),
            "cv_percent": statistics.stdev(values) / mean * 100 if len(values) > 1 and mean else None, "passes": len(values)}


def environment_info(path):
    info = {"os": platform.platform(), "architecture": platform.machine(), "cpu": platform.processor(),
            "logical_cpus": os.cpu_count(), "python": platform.python_version(), "storage": disk_usage(path),
            "scope": "filesystem_file", "device_steady_state_verified": False, "power_mode": "not_measured", "pcie_link": "not_measured"}
    if platform.system() == "Darwin":
        for key, parameter in (("hardware_model", "hw.model"), ("ram_bytes", "hw.memsize")):
            try:
                proc = command(["/usr/sbin/sysctl", "-n", parameter], 3)
                if proc.returncode == 0:
                    info[key] = int(proc.stdout.strip()) if key == "ram_bytes" else proc.stdout.strip()
            except AppError:
                pass
    return info


def default_data_dir():
    if os.environ.get("SSD_LAB_DATA_DIR"):
        return Path(os.environ["SSD_LAB_DATA_DIR"])
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "SSD Lab"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SSD Lab"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "ssd-lab"


class Lab:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.settings = self.read_file("settings.json", {"fio_path": "", "smartctl_path": ""})
        self.history = self.read_file("history.json", [])
        self.dependencies = {}
        self.devices = {}
        self.proc = None
        self.plot_data = {"id": None, "series": []}
        self.cancel = threading.Event()
        self.worker = None
        self.state = {"status": "idle", "stage": "Awaiting test", "progress": 0, "results": {}, "logs": [], "error": None}
        self.refresh_dependencies()

    def read_file(self, name, fallback):
        try:
            return json.loads((self.data_dir / name).read_text("utf-8"))
        except (OSError, ValueError):
            return fallback

    def save_file(self, name, data):
        target = self.data_dir / name
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(target)

    def refresh_dependencies(self):
        found = {name: dependency(name, self.settings.get(name + "_path", "")) for name in ("fio", "smartctl")}
        with self.lock:
            self.dependencies = found
        return found

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            result["dependencies"] = copy.deepcopy(self.dependencies)
            result["running"] = bool(self.worker and self.worker.is_alive())
            result["platform"] = platform.system()
            result["version"] = VERSION
            return result

    def log(self, message):
        with self.lock:
            self.state["logs"].append({"time": datetime.now().strftime("%H:%M:%S"), "message": message})
            self.state["logs"] = self.state["logs"][-150:]

    def save_settings(self, payload):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise AppError("Tool paths cannot be changed during a test.")
            settings = {}
            for key in ("fio_path", "smartctl_path"):
                value = payload.get(key, "")
                if not isinstance(value, str) or len(value) > 4096:
                    raise AppError("Invalid tool path.")
                if value and not Path(value).is_absolute():
                    raise AppError("Tool paths must be absolute.")
                settings[key] = value
            self.settings = settings
            self.save_file("settings.json", self.settings)
        return self.refresh_dependencies()

    def scan_smart(self, elevated=False):
        dep = self.dependencies["smartctl"]
        if not dep["available"]:
            raise AppError("Install smartmontools or configure the smartctl path.")
        raw, _ = smart_command([dep["path"], "--scan-open", "-j"], elevated)
        devices = raw.get("devices", [])
        found = {}
        for device in devices:
            name = device.get("name")
            dtype = device.get("type")
            if isinstance(name, str) and name and not name.startswith("-"):
                found[name] = {"name": name, "type": dtype, "protocol": device.get("protocol"), "info": device.get("info_name", name)}
        with self.lock:
            self.devices = found
        messages = [v.get("string", "") for v in (raw.get("smartctl") or {}).get("messages", [])]
        return {"devices": list(found.values()), "messages": messages,
                "warning": "No readable physical devices; administrator access may be needed or SMART may be unsupported." if not found else None}

    def get_smart(self, name, elevated=False, error_log=False):
        if type(error_log) is not bool:
            raise AppError("Invalid error log mode.")
        with self.lock:
            device = self.devices.get(name)
            dep = copy.deepcopy(self.dependencies["smartctl"])
        if not device or not dep["available"]:
            raise AppError("Scan devices first, then select a result.")
        # Health (NVMe log 0x02) is independent of the optional error log
        # (0x01). -a requests both and some Apple controllers reject the latter.
        args = [dep["path"], "-i", "-H", "-A", "-j"]
        if device.get("type"):
            args += ["-d", device["type"]]
        args += [device["name"]]
        raw, code = smart_command(args, elevated)
        # smartctl uses a bitmask: a nonzero status may contain valid health data.
        result = smart_summary(raw, code)
        result["nand"] = apple_nand_info(name) if result["model"].upper().startswith("APPLE SSD") else {"status": "not_applicable", "supplier": None}
        result["error_log"] = {"status": "not_requested"}
        if error_log:
            log_args = [dep["path"], "-l", "error", "-j"]
            if device.get("type"):
                log_args += ["-d", device["type"]]
            log_args += [device["name"]]
            try:
                log, log_code = smart_command(log_args, elevated)
                result["error_log"] = {"status": "failed" if log_code & 7 else "read",
                                       "exit_code": log_code, "raw": log,
                                       "messages": [item.get("string", "") for item in (log.get("smartctl") or {}).get("messages", [])]}
            except AppError as exc:
                result["error_log"] = {"status": "failed", "messages": [str(exc)]}
        return result

    def validate_run(self, payload):
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            raise AppError("Choose a test folder.")
        expanded = Path(path).expanduser()
        if not expanded.is_absolute():
            raise AppError("Enter an absolute folder path.")
        target = expanded.resolve()
        if not target.is_dir() or not target.is_absolute():
            raise AppError("The target must be an existing folder, not a device or file.")
        if str(target).startswith("/dev/") or str(target) == "/dev":
            raise AppError("Raw device paths are not allowed.")
        # fio treats colons as multiple filenames even when shell=False.
        if any(char in str(target) for char in ("\n", "\r")) or (os.name != "nt" and ":" in str(target)):
            raise AppError("Test folder paths cannot contain colons or line breaks.")
        if not os.access(target, os.W_OK):
            raise AppError("The folder is not writable.")
        profile = payload.get("profile", "standard")
        if profile not in PROFILES:
            raise AppError("Unknown test profile.")
        allowed = {"size_mib": (64, 256, 1024, 4096, 16384), "duration": (1, 3, 5, 10, 30), "passes": (1, 2, 3, 5)}
        normalized = {"path": str(target), "profile": profile}
        for key, options in allowed.items():
            value = payload.get(key, {"size_mib": 1024, "duration": 5, "passes": 1}[key])
            if type(value) is not int or value not in options:
                raise AppError(f"Invalid {key} parameter.")
            normalized[key] = value
        if type(payload.get("direct", True)) is not bool:
            raise AppError("Invalid cache setting.")
        normalized["direct"] = payload.get("direct", True)
        selected = payload.get("row", "all")
        if selected != "all" and selected not in [row["id"] for row in PROFILES[profile]]:
            raise AppError("Unknown test case.")
        normalized["row"] = selected
        engine = payload.get("engine", "auto")
        if engine not in ("auto", "posixaio", "psync", "sync"):
            raise AppError("Unsupported I/O engine. Choose posixaio / psync / sync.")
        chosen_rows = [row for row in PROFILES[profile] if selected == "all" or selected == row["id"]]
        if engine in ("psync", "sync") and any(row["depth"] > 1 for row in chosen_rows):
            raise AppError("psync / sync are synchronous with effective QD1. Use the POSIX profile or posixaio for QD sweeps.")
        normalized["engine"] = engine
        plot_mode = payload.get("plot_mode", "window")
        if plot_mode not in ("off", "window", "per_io"):
            raise AppError("Invalid logging mode.")
        normalized["plot_mode"] = plot_mode
        size_bytes = normalized["size_mib"] * 1024 * 1024
        free = shutil.disk_usage(target).free
        reserve = max(256 * 1024 * 1024, int(size_bytes * 0.1))
        if free < size_bytes + reserve:
            raise AppError("Not enough free space. Allow for the test file and at least 256 MiB spare.")
        return normalized

    def start(self, payload):
        config = self.validate_run(payload)
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise AppError("A test is already running.")
            dep = copy.deepcopy(self.dependencies["fio"])
            if not dep["available"]:
                raise AppError("Install fio or configure its path in Settings.")
            self.cancel.clear()
            self.state = {"status": "running", "stage": "Preparing test file", "progress": 0, "results": {},
                          "logs": [], "error": None, "config": config, "current": None,
                          "plot_revision": 0,
                          "started_at": datetime.now(timezone.utc).isoformat()}
            self.worker = threading.Thread(target=self.run, args=(config, dep), daemon=True)
            self.worker.start()
        return self.snapshot()

    def stop(self):
        self.cancel.set()
        with self.lock:
            proc = self.proc
            if self.worker and self.worker.is_alive():
                self.state["stage"] = "Stopping and cleaning up"
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        return {"ok": True}

    def invoke_fio(self, args, duration, start_fraction, end_fraction, preparing=False):
        if self.cancel.is_set():
            raise Cancelled()
        with tempfile.TemporaryFile(mode="w+b") as stdout, tempfile.TemporaryFile(mode="w+b") as stderr:
            with self.lock:
                if self.cancel.is_set():
                    raise Cancelled()
                proc = subprocess.Popen(args, stdout=stdout, stderr=stderr,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.proc = proc
            start = time.monotonic()
            try:
                while proc.poll() is None:
                    elapsed = time.monotonic() - start
                    if self.cancel.wait(0.1):
                        try:
                            proc.terminate()
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=3)
                        raise Cancelled()
                    limit = 600 if preparing else duration + 60
                    if elapsed > limit:
                        proc.kill()
                        proc.wait(timeout=5)
                        raise AppError("fio timed out; the test was terminated.")
                    if not preparing:
                        fraction = min(0.96, elapsed / max(duration, 1))
                        with self.lock:
                            self.state["progress"] = round(100 * (start_fraction + (end_fraction - start_fraction) * fraction), 1)
                if self.cancel.is_set():
                    raise Cancelled()
                stdout.seek(0)
                stderr.seek(0)
                output = stdout.read().decode("utf-8", "replace")
                error = stderr.read().decode("utf-8", "replace")
                if proc.returncode:
                    start_json = re.search(r"(?m)^\s*\{\s*$", output)
                    banner = output[:start_json.start()].strip() if start_json else output[:1500].strip()
                    details = []
                    try:
                        failed = parse_json_output(output)
                        details = [f"I/O error {job['error']}: {os.strerror(job['error'])}" for job in failed.get("jobs", []) if job.get("error")]
                    except (AppError, TypeError, ValueError):
                        pass
                    message = "\n".join(part for part in [error.strip()[:1500], banner[:1500], *details] if part) or f"fio exit code {proc.returncode}"
                    raise AppError(message, {"command": args, "stdout": output, "stderr": error, "exit_code": proc.returncode})
                raw = parse_json_output(output)
                if any(job.get("error", 0) for job in raw.get("jobs", [])):
                    raise AppError("fio reported an I/O failure. Check the filesystem and Direct I/O support.")
                return raw
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                with self.lock:
                    self.proc = None

    def run(self, config, dep):
        scratch = None
        record = {"id": secrets.token_hex(8), "created_at": self.state["started_at"], "config": config,
                  "platform": platform.platform(), "fio_version": dep["version"], "results": {}, "raw": [],
                  "status": "running", "io_bytes": 0, "preparation_bytes": config["size_mib"] * 1024 * 1024}
        try:
            with self.lock:
                self.plot_data = {"id": record["id"], "series": []}
                self.state["plot_id"] = record["id"]
            record["environment"] = environment_info(config["path"])
            record["methodology"] = {"scope": "file_level", "device_purge": "not_performed", "device_preconditioning": "not_performed",
                                     "file_preparation": "one_full_sequential_write", "steady_state": "not_verified", "ramp_seconds": 0 if config["plot_mode"] == "per_io" else 1,
                                     "data_pattern": "random_buffers", "latency": "fio_total_latency", "snia_compliance": False}
            record["methodology"]["logging"] = config["plot_mode"]
            record["methodology"]["per_io_limit_per_stage"] = 200000 if config["plot_mode"] == "per_io" else None
            record["methodology"]["logging_overhead"] = "may_affect_measurements"
            # A fresh private directory prevents overwriting user files or following pre-existing symlinks.
            scratch = Path(tempfile.mkdtemp(prefix=".ssd-lab-", dir=config["path"]))
            filename = scratch / "benchmark.bin"
            descriptor = os.open(filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            if not stat.S_ISREG(filename.stat().st_mode):
                raise AppError("The temporary target is not a regular file.")
            fio_filename = str(filename).replace("\\", "/")
            if os.name == "nt":
                fio_filename = fio_filename.replace(":", "\\:")
            engine = {"Darwin": "posixaio", "Linux": "libaio", "Windows": "windowsaio"}.get(platform.system(), "posixaio")
            if platform.system() == "Linux" and not config["direct"]:
                engine = "posixaio"
            if config["engine"] != "auto":
                engine = config["engine"]
            elif config["profile"] == "posix":
                engine = "psync"
            record["ioengine"] = engine
            common = [dep["path"], "--output-format=json+", "--eta=never", f"--filename={fio_filename}",
                      f"--direct={int(config['direct'])}", "--thread=1", "--group_reporting=1",
                      "--refill_buffers=1", "--buffer_compress_percentage=0", "--randrepeat=0", "--invalidate=1",
                      "--lat_percentiles=1", "--clat_percentiles=0", "--percentile_list=50:95:99:99.9:99.99:99.999"]
            size = config["size_mib"] * 1024 * 1024
            self.log(f"fio {dep['version']} · {engine} · {config['size_mib']} MiB")
            self.log("Preparing the complete file; read tests also need initial data writes.")
            preparation = common + ["--name=prepare", "--ioengine=sync", "--rw=write", "--bs=1M", f"--size={size}",
                                    "--numjobs=1", "--iodepth=1", "--end_fsync=1", "--fallocate=none"]
            raw = self.invoke_fio(preparation, 0, 0, 0, True)
            record["raw"].append({"stage": "prepare", "command": preparation, "output": raw})
            self.log("Test file prepared.")
            rows = [row for row in PROFILES[config["profile"]] if config["row"] == "all" or row["id"] == config["row"]]
            total = sum(1 if "mix_read" in row else 2 for row in rows) * config["passes"]
            completed = 0
            for row in rows:
                for direction in (("mixed",) if "mix_read" in row else ("read", "write")):
                    samples = {"read": [], "write": []}
                    best_pair = None
                    for repeat in range(config["passes"]):
                        if self.cancel.is_set():
                            raise Cancelled()
                        operation = "randrw" if direction == "mixed" else ("rand" if row["random"] else "") + direction
                        direction_label = {"read": "Read", "write": "Write", "mixed": "Mixed I/O"}[direction]
                        stage = f"{row['label']} {row['detail']} · {direction_label} · {repeat+1}/{config['passes']}"
                        with self.lock:
                            self.state.update(stage=stage, current={"row": row["id"], "direction": direction})
                        self.log(stage)
                        segment_size = size // row["threads"]
                        args = common + [f"--name={row['id']}-{direction}", f"--ioengine={engine}", f"--rw={operation}",
                                         f"--bs={row['bs']}", f"--iodepth={row['depth']}", f"--numjobs={row['threads']}",
                                         f"--size={segment_size}", f"--offset_increment={segment_size}", "--allow_file_create=0",
                                         "--overwrite=1", "--time_based=1", f"--runtime={config['duration']}",
                                         f"--ramp_time={0 if config['plot_mode'] == 'per_io' else 1}"]
                        if direction == "write":
                            args.append("--end_fsync=1")
                        elif direction == "read":
                            args.append("--readonly")
                        else:
                            args.extend([f"--rwmixread={row['mix_read']}", "--end_fsync=1"])
                        if config["plot_mode"] != "off":
                            with tempfile.TemporaryDirectory(prefix="ssd-lab-fio-logs-") as logs:
                                prefix = str(Path(logs) / "trace").replace("\\", "/")
                                args += [f"--write_lat_log={prefix}", "--log_offset=1", "--log_unix_epoch=1", "--per_job_logs=1", "--log_entries=65536"]
                                if config["plot_mode"] == "window":
                                    args += ["--log_avg_msec=10", f"--write_bw_log={prefix}", f"--write_iops_log={prefix}"]
                                else:
                                    args += ["--log_avg_msec=0", f"--number_ios={200000 // row['threads']}"]
                                raw = self.invoke_fio(args, config["duration"] + 1, completed / total, (completed + 1) / total)
                                context = {"row": row["id"], "label": row["label"] + " " + row["detail"], "pass": repeat + 1,
                                           "queue_depth": row["depth"], "threads": row["threads"], "engine": engine}
                                series = parse_logs(logs, context, config["plot_mode"])
                                with self.lock:
                                    self.plot_data["series"].append(series)
                                    bound_series(self.plot_data["series"])
                                    self.state["plot_revision"] += 1
                                if series["log_rows"] == 0:
                                    self.log("fio produced no plottable logs; no synthetic points are generated.")
                        else:
                            raw = self.invoke_fio(args, config["duration"] + 1, completed / total, (completed + 1) / total)
                        measured_directions = ("read", "write") if direction == "mixed" else (direction,)
                        pair = {measured: fio_metrics(raw, measured) for measured in measured_directions}
                        for measured, sample in pair.items():
                            samples[measured].append(sample)
                            record["io_bytes"] += sample["io_bytes"]
                        record["raw"].append({"row": row["id"], "direction": direction, "pass": repeat + 1, "command": args, "output": raw})
                        if best_pair is None or sum(value["mb_s"] for value in pair.values()) > sum(value["mb_s"] for value in best_pair.values()):
                            best_pair = pair
                        for measured in measured_directions:
                            best = dict(best_pair[measured])
                            best["samples"] = copy.deepcopy(samples[measured])
                            best["statistics"] = sample_statistics(samples[measured])
                            record["results"].setdefault(row["id"], {"label": row["label"], "detail": row["detail"]})[measured] = best
                        completed += 1
                        with self.lock:
                            self.state["results"] = copy.deepcopy(record["results"])
                            self.state["progress"] = round(completed / total * 100, 1)
                        for measured, sample in pair.items():
                            self.log(f"{measured} · {sample['mb_s']:.2f} MB/s · {sample['iops']:.0f} IOPS · {sample['latency_us']:.2f} μs")
            record["status"] = "completed"
        except Cancelled:
            record["status"] = "cancelled"
            self.log("Test stopped; completed results are retained.")
        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            if getattr(exc, "diagnostics", None):
                record["failure_diagnostics"] = exc.diagnostics
            self.log("Test failed: " + str(exc))
        finally:
            if scratch:
                try:
                    shutil.rmtree(scratch)
                    self.log("Temporary test files removed.")
                except OSError as exc:
                    record["cleanup_warning"] = f"Unable to remove temporary files: {scratch} ({exc})"
                    self.log(record["cleanup_warning"])
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            with self.lock:
                self.history.insert(0, record)
                self.history = self.history[:100]
                try:
                    if any(series["latency_points"] or series["flow_points"] for series in self.plot_data["series"]):
                        plot_dir = self.data_dir / "plots"
                        plot_dir.mkdir(exist_ok=True)
                        (plot_dir / (record["id"] + ".json")).write_text(json.dumps(self.plot_data, ensure_ascii=False), "utf-8")
                        record["plot_available"] = True
                    self.save_file("history.json", self.history)
                except OSError as exc:
                    self.log("Unable to save test history: " + str(exc))
                self.state.update(status=record["status"], stage={"completed": "Test complete", "cancelled": "Test stopped", "error": "Test failed"}[record["status"]],
                                  current=None, error=record.get("error"), record_id=record["id"], cleanup_warning=record.get("cleanup_warning"))

    def get_history(self):
        with self.lock:
            return [{k: copy.deepcopy(v) for k, v in record.items() if k not in ("raw", "failure_diagnostics")} for record in self.history]

    def get_plots(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{16}", identifier):
            raise AppError("Invalid record ID.")
        with self.lock:
            if self.plot_data["id"] == identifier:
                return copy.deepcopy(self.plot_data)
            if not any(record["id"] == identifier for record in self.history):
                raise AppError("Test record not found.")
        path = self.data_dir / "plots" / (identifier + ".json")
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return {"id": identifier, "series": []}

    def report(self, identifier, format_name):
        with self.lock:
            record = next((copy.deepcopy(record) for record in self.history if record["id"] == identifier), None)
        if not record:
            raise AppError("Test record not found.")
        if format_name == "json":
            if record.get("plot_available"):
                record["scatter_data"] = self.get_plots(identifier)
            return json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"), "application/json", "json"
        if format_name == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Time", "Path", "Profile", "Status", "fio", "Engine", "DirectIO", "SizeMiB", "Duration", "Passes", "Test", "QueueThreads", "Direction", "Best_MB/s", "IOPS", "Mean_Total_Latency_us", "P99_Total_us", "P99.9_Total_us", "P99.99_Total_us", "Median_MB/s", "CV_percent", "Steady_State_Verified"])
            for row in record["results"].values():
                for direction in ("read", "write"):
                    value = row.get(direction)
                    if value:
                        path = record["config"]["path"]
                        # CSV formula-injection guard for user-controlled strings.
                        if path.startswith(("=", "+", "-", "@")):
                            path = "'" + path
                        writer.writerow([record["created_at"], path, record["config"]["profile"], record["status"], record["fio_version"],
                                         record.get("ioengine"), record["config"]["direct"], record["config"]["size_mib"], record["config"]["duration"],
                                         record["config"]["passes"], row["label"], row["detail"], direction,
                                         f'{value["mb_s"]:.3f}', f'{value["iops"]:.3f}', f'{value["latency_us"]:.3f}',
                                         value.get("p99_us"), value.get("p999_us"), value.get("p9999_us"),
                                         value.get("statistics", {}).get("median_mb_s"), value.get("statistics", {}).get("cv_percent"), False])
            return ("\ufeff" + output.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", "csv"
        raise AppError("Unsupported export format.")


def choose_directory():
    system = platform.system()
    if system == "Darwin":
        proc = command(["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose SSD Lab test folder")'], timeout=120)
        return {"path": proc.stdout.strip() if proc.returncode == 0 else None}
    if system == "Windows":
        script = "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='SSD Lab'; if($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"
        proc = command(["powershell", "-NoProfile", "-STA", "-Command", script], timeout=120)
        return {"path": proc.stdout.strip() or None}
    if shutil.which("zenity"):
        proc = command(["zenity", "--file-selection", "--directory", "--title=SSD Lab"], timeout=120)
        return {"path": proc.stdout.strip() if proc.returncode == 0 else None}
    raise AppError("No folder picker is available; enter an absolute folder path.")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log the session token or private device data.

    def reply(self, data, status=200, mime="application/json; charset=utf-8", filename=None):
        payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authenticated(self):
        expected_host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected_host:
            self.reply({"error": "Invalid Host"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{expected_host}":
            self.reply({"error": "Cross-origin request rejected."}, 403)
            return False
        if not secrets.compare_digest(self.headers.get("X-SSD-Token", ""), self.server.token):
            self.reply({"error": "Session expired; reopen the app from its launcher."}, 401)
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/scatter.js": ("scatter.js", "text/javascript; charset=utf-8"),
                      "/styles.css": ("styles.css", "text/css; charset=utf-8"), "/glass.css": ("glass.css", "text/css; charset=utf-8"),
                      "/appearance.js": ("appearance.js", "text/javascript; charset=utf-8"), "/locales.js": ("locales.js", "text/javascript; charset=utf-8"),
                      "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"), "/icon.svg": ("icon.svg", "image/svg+xml")}
            if path not in assets:
                return self.reply({"error": "Not found"}, 404)
            name, mime = assets[path]
            return self.reply((ROOT / "web" / name).read_bytes(), mime=mime)
        if not self.authenticated():
            return
        try:
            lab = self.server.lab
            if path == "/api/state":
                data = lab.snapshot()
            elif path == "/api/volumes":
                data = scan_volumes()
            elif path == "/api/settings":
                data = {"settings": lab.settings, "dependencies": lab.dependencies}
            elif path == "/api/history":
                data = {"records": lab.get_history()}
            elif path == "/api/plots":
                data = lab.get_plots(parse_qs(parsed.query).get("id", [""])[0])
            elif path == "/api/export":
                query = parse_qs(parsed.query)
                payload, mime, suffix = lab.report(query.get("id", [""])[0], query.get("format", ["json"])[0])
                return self.reply(payload, mime=mime, filename="SSD-Lab-report." + suffix)
            else:
                return self.reply({"error": "Not found"}, 404)
            self.reply(data)
        except AppError as exc:
            self.reply({"error": str(exc)}, 400)
        except Exception as exc:
            self.reply({"error": str(exc)}, 500)

    def do_POST(self):
        if not self.authenticated():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16384:
                return self.reply({"error": "Request too large"}, 413)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise AppError("Request must be a JSON object.")
            path = urlparse(self.path).path
            lab = self.server.lab
            if path == "/api/start":
                result = lab.start(data)
            elif path == "/api/stop":
                result = lab.stop()
            elif path == "/api/settings":
                result = {"dependencies": lab.save_settings(data)}
            elif path == "/api/dependencies/refresh":
                result = {"dependencies": lab.refresh_dependencies()}
            elif path == "/api/smart/scan":
                result = lab.scan_smart(data.get("elevated", False))
            elif path == "/api/smart/read":
                result = lab.get_smart(data.get("device"), data.get("elevated", False), data.get("error_log", False))
            elif path == "/api/pick-folder":
                result = choose_directory()
            elif path == "/api/path-info":
                target = Path(data.get("path", "")).expanduser().resolve()
                if not target.is_dir():
                    raise AppError("Folder does not exist.")
                result = {"path": str(target), **disk_usage(target)}
            else:
                return self.reply({"error": "Not found"}, 404)
            self.reply(result)
        except (AppError, ValueError, TypeError) as exc:
            self.reply({"error": str(exc)}, 400)
        except Exception as exc:
            self.reply({"error": str(exc)}, 500)


def main():
    parser = argparse.ArgumentParser(description="SSD Lab — fio + SMART desktop workbench")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args()
    lab = Lab(args.data_dir)
    server = Server(("127.0.0.1", args.port), Handler)
    server.lab = lab
    server.token = secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{server.server_port}/?token={server.token}"
    print(json.dumps({"url": url, "pid": os.getpid(), "version": VERSION}), flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    def shutdown(*_):
        lab.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        lab.stop()
        if lab.worker:
            lab.worker.join(timeout=8)
        server.server_close()


if __name__ == "__main__":
    main()
