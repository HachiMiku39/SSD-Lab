"""Parse real fio logs into bounded, explicitly sampled scatter datasets."""
import csv
import heapq
import math
import random
from pathlib import Path


def parse_logs(directory, context, mode, budget=2000):
    directory = Path(directory)
    rng = random.Random(8128 + context["pass"])
    reservoir, slowest, flows = [], [], {}
    total = invalid = 0
    first_time = last_time = None
    for file in sorted(directory.glob("trace_lat.*.log")):
        job = int(file.name.split(".")[-2])
        with file.open(encoding="utf-8", errors="replace") as stream:
            for values in csv.reader(stream):
                try:
                    stamp, value, direction, block = float(values[0]), float(values[1]), int(values[2]), float(values[3])
                    offset = float(values[4]) if len(values) > 4 else None
                    if direction not in (0, 1) or not all(math.isfinite(v) for v in (stamp, value, block)) or value < 0 or (offset is not None and not math.isfinite(offset)):
                        raise ValueError()
                except (ValueError, IndexError):
                    invalid += 1
                    continue
                first_time = stamp if first_time is None else min(first_time, stamp)
                last_time = stamp if last_time is None else max(last_time, stamp)
                # fio times are milliseconds, latencies nanoseconds, offsets bytes.
                point = [stamp, value / 1000, direction, offset / 1048576 if mode == "per_io" and offset is not None else None,
                         block / 1024 if mode == "per_io" else None, job]
                total += 1
                if len(reservoir) < budget:
                    reservoir.append(point)
                else:
                    index = rng.randrange(total)
                    if index < budget:
                        reservoir[index] = point
                item = (point[1], total, point)
                if len(slowest) < 20:
                    heapq.heappush(slowest, item)
                elif item[0] > slowest[0][0]:
                    heapq.heapreplace(slowest, item)
                if mode == "per_io":
                    key = (int(stamp // 100) * 100, direction, 0)
                    value = flows.setdefault(key, [0, 0])
                    value[0] += block
                    value[1] += 1
    flow_points = []
    if mode == "window":
        flows = {}
        for kind, field in (("bw", 0), ("iops", 1)):
            for file in sorted(directory.glob(f"trace_{kind}.*.log")):
                job = int(file.name.split(".")[-2])
                with file.open(encoding="utf-8", errors="replace") as stream:
                    for values in csv.reader(stream):
                        try:
                            stamp, value, direction = float(values[0]), float(values[1]), int(values[2])
                            if direction not in (0, 1) or not all(math.isfinite(v) for v in (stamp, value)):
                                raise ValueError()
                            entry = flows.setdefault((stamp, direction, job), [None, None])
                            entry[field] = value * 1024 / 1_000_000 if field == 0 else value
                            first_time = stamp if first_time is None else min(first_time, stamp)
                            last_time = stamp if last_time is None else max(last_time, stamp)
                        except (ValueError, IndexError):
                            invalid += 1
        flow_points = [[stamp, values[0], values[1], direction, job] for (stamp, direction, job), values in flows.items()]
    elif first_time is not None:
        for (stamp, direction, job), (byte_count, operations) in flows.items():
            duration_ms = max(1, min(stamp + 100, last_time + 1) - max(stamp, first_time))
            flow_points.append([max(stamp, first_time), byte_count / duration_ms / 1000, operations * 1000 / duration_ms, direction, job])
    points = reservoir if total <= budget else reservoir + [item[2] for item in slowest]
    if first_time is not None:
        for point in points + flow_points:
            point[0] = (point[0] - first_time) / 1000
    if len(flow_points) > budget:
        flow_points = rng.sample(flow_points, budget)
    return {**context, "mode": mode, "latency_points": points, "flow_points": flow_points,
            "log_rows": total, "invalid_rows": invalid, "display_rows": len(points),
            "sampling": "all" if total <= budget else "reservoir_plus_slowest_20",
            "time_origin": "first_logged_event", "offset_kind": "test_file_byte_offset",
            "flow_source": "fio_10ms_window_logs_per_worker" if mode == "window" else "completed_io_100ms_windows_all_workers"}


def bound_series(series, maximum=30000):
    budget = max(20, maximum // max(1, len(series)))
    rng = random.Random(222)
    for item in series:
        points = item["latency_points"]
        if len(points) > budget:
            outliers = sorted(points, key=lambda point: point[1], reverse=True)[:20]
            item["latency_points"] = rng.sample(points, max(0, budget - 20)) + outliers
            item["sampling"] = "bounded_reservoir_plus_slowest_20"
        if len(item["flow_points"]) > budget:
            item["flow_points"] = rng.sample(item["flow_points"], budget)
        item["display_rows"] = len(item["latency_points"])
