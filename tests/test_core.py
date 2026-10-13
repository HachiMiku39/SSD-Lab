import importlib.util
import json
import pathlib
import sys
import tempfile
import time
import unittest
import types
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import apple_nand
spec = importlib.util.spec_from_file_location("ssd_lab", ROOT / "app.py")
lab_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab_module)


class ParserTests(unittest.TestCase):
    def test_bandwidth_is_decimal_mb_and_weighted_total_latency(self):
        raw = {"jobs": [{"read": {"bw": 1024, "iops": 5, "total_ios": 10, "lat_ns": {"mean": 2000}, "io_bytes": 40960}},
                        {"read": {"bw_bytes": 1000000, "iops": 8, "total_ios": 30, "lat_ns": {"mean": 6000}}}]}
        value = lab_module.fio_metrics(raw, "read")
        self.assertAlmostEqual(value["mb_s"], 2.048576)
        self.assertEqual(value["iops"], 13)
        self.assertEqual(value["latency_us"], 5)

    def test_invalid_io_cannot_be_a_successful_result(self):
        for raw in ({"jobs": []}, {"jobs": [{"error": 5}]}, {"jobs": [{"read": {"total_ios": 0}}]}):
            with self.assertRaises(lab_module.AppError):
                lab_module.fio_metrics(raw, "read")

    def test_fio_banner_before_json(self):
        self.assertEqual(lab_module.parse_json_output('fio: starting threads\n{"jobs": []}'), {"jobs": []})

    def test_nvme_health_uses_reported_life(self):
        result = lab_module.smart_summary({"smart_status": {"passed": True}, "nvme_smart_health_information_log": {
            "percentage_used": 8, "temperature": 36, "data_units_written": 1234, "critical_warning": 0}}, 0)
        self.assertEqual(result["health"], "good")
        self.assertEqual(result["remaining"], 92)
        self.assertEqual(result["write_bytes"], 1234 * 512000)

    def test_smart_nonzero_health_status_is_still_parsed(self):
        result = lab_module.smart_summary({"model_name": "Test SSD", "smart_status": {"passed": False}}, 8)
        self.assertEqual(result["model"], "Test SSD")
        self.assertEqual(result["health"], "bad")
        self.assertFalse(result["partial"])

    def test_missing_health_is_unknown_and_ata_life_is_not_guessed(self):
        result = lab_module.smart_summary({"ata_smart_attributes": {"table": [{"id": 202, "value": 99, "name": "Vendor_Lifetime"}]}}, 2)
        self.assertEqual(result["health"], "unknown")
        self.assertIsNone(result["remaining"])
        self.assertTrue(result["partial"])

    def test_nvme_critical_warning_takes_precedence(self):
        result = lab_module.smart_summary({"smart_status": {"passed": True}, "nvme_smart_health_information_log": {"critical_warning": 1}}, 0)
        self.assertEqual(result["health"], "warning")

    def test_optional_error_log_failure_does_not_mean_failed_health(self):
        result = lab_module.smart_summary({"smart_status": {"passed": True}, "nvme_smart_health_information_log": {
            "critical_warning": 0, "media_errors": 0, "num_err_log_entries": 0}, "smartctl": {"messages": [{
                "string": "Read 1 entries from Error Information Log failed: GetLogPage failed: system=0x38, sub=0x0, code=745"}]}}, 4)
        self.assertEqual(result["health"], "good")
        self.assertTrue(result["health_data_available"])
        self.assertTrue(result["partial"])

    def test_successful_command_without_health_is_unknown(self):
        result = lab_module.smart_summary({"model_name": "SSD"}, 0)
        self.assertEqual(result["health"], "unknown")
        self.assertFalse(result["health_data_available"])
        self.assertTrue(result["messages"])

    def test_used_over_100_is_clamped_and_warns(self):
        result = lab_module.smart_summary({"smart_status": {"passed": True}, "nvme_smart_health_information_log": {"percentage_used": 120}}, 0)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["health"], "warning")

    def test_elevated_smart_preserves_smartctl_bitmask(self):
        proc = types.SimpleNamespace(returncode=0, stdout='{"smartctl":{"exit_status":8},"smart_status":{"passed":false}}', stderr="")
        with patch.object(lab_module.platform, "system", return_value="Darwin"), patch.object(lab_module, "command", return_value=proc) as runner:
            raw, code = lab_module.smart_command(["/safe/tool's path/smartctl", "-a", "-j", "/dev/disk0"], True)
            self.assertEqual(code, 8)
            script = runner.call_args.args[0][-1]
            self.assertIn("with administrator privileges", script)
            self.assertEqual(lab_module.smart_summary(raw, code)["health"], "bad")

    def test_total_latency_percentiles_keep_their_units(self):
        raw = {"jobs": [{"read": {"bw_bytes": 1000000, "total_ios": 10000, "iops": 100,
                 "lat_ns": {"mean": 2000, "max": 90000, "percentile": {"99.000000": 17000, "99.990000": 88000}},
                 "clat_ns": {"percentile": {"99.000000": 6000}}}}]}
        result = lab_module.fio_metrics(raw, "read")
        self.assertEqual(result["p99_us"], 17)
        self.assertEqual(result["p9999_us"], 88)
        self.assertEqual(result["max_latency_us"], 90)
        self.assertTrue(result["tail_sample_warning"])

    def test_job_percentiles_are_not_averaged(self):
        job = {"read": {"bw_bytes": 1000000, "total_ios": 1000, "lat_ns": {"mean": 1000, "percentile": {"99": 9000}}}}
        self.assertIsNone(lab_module.fio_metrics({"jobs": [job, job]}, "read")["p99_us"])

    def test_repeated_results_report_median_and_variability(self):
        result = lab_module.sample_statistics([{"mb_s": 10}, {"mb_s": 11}, {"mb_s": 30}])
        self.assertEqual(result["median_mb_s"], 11)
        self.assertEqual(result["max_mb_s"], 30)
        self.assertGreater(result["cv_percent"], 0)
        self.assertIsNone(lab_module.sample_statistics([{"mb_s": 10}])["cv_percent"])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.directory.name)
        self.target = self.root / "target"
        self.target.mkdir()
        self.sentinel = self.target / "user-data.bin"
        self.sentinel.write_bytes(b"must remain intact")
        self.lab = lab_module.Lab(self.root / "state")
        self.config = {"path": str(self.target), "size_mib": 64, "duration": 1, "passes": 1,
                       "profile": "standard", "row": "rnd1", "direct": True}

    def tearDown(self):
        self.lab.stop()
        if self.lab.worker:
            self.lab.worker.join(5)
        self.directory.cleanup()

    def fake_tool(self, delay=0, fail=False):
        # A subprocess fixture exercises process lifetime, cancellation, and error paths.
        path = self.root / "fixture-fio"
        source = f'''#!{sys.executable}
import json, sys, time
if "--version" in sys.argv:
    print("fio-3.43"); raise SystemExit(0)
time.sleep({delay})
if {fail!r}:
    print("fixture I/O error", file=sys.stderr); raise SystemExit(5)
direction = "read" if any(a.startswith("--rw=") and "read" in a for a in sys.argv) else "write"
metric={{"bw_bytes":1000000,"iops":244,"total_ios":1000,"io_bytes":4096000,"lat_ns":{{"mean":20000}}}}
job={{"read":metric,"write":metric}} if "--rw=randrw" in sys.argv else {{direction:metric}}
print(json.dumps({{"jobs":[job]}}))
'''
        path.write_text(source)
        path.chmod(0o755)
        self.lab.dependencies["fio"] = {"available": True, "path": str(path), "version": "3.43"}

    def wait(self):
        self.lab.worker.join(10)
        self.assertFalse(self.lab.worker.is_alive())

    def assert_clean(self):
        self.assertEqual(self.sentinel.read_bytes(), b"must remain intact")
        self.assertEqual(list(self.target.iterdir()), [self.sentinel])

    def test_success_persists_and_exports_raw_fio(self):
        self.fake_tool()
        self.lab.start(self.config)
        self.wait()
        self.assertEqual(self.lab.state["status"], "completed")
        self.assertEqual(len(self.lab.history[0]["raw"]), 3)
        commands = self.lab.history[0]["raw"]
        self.assertIn("--readonly", commands[1]["command"])
        self.assertIn("--allow_file_create=0", commands[1]["command"])
        raw, mime, suffix = self.lab.report(self.lab.history[0]["id"], "json")
        self.assertEqual(json.loads(raw)["status"], "completed")
        csv_bytes, _, _ = self.lab.report(self.lab.history[0]["id"], "csv")
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        restarted = lab_module.Lab(self.root / "state")
        self.assertEqual(len(restarted.history), 1)
        self.assert_clean()

    def test_mixed_workload_keeps_both_directions_and_rounds(self):
        self.fake_tool()
        self.lab.start({**self.config, "profile": "qos", "row": "mix16", "passes": 2})
        self.wait()
        self.assertEqual(self.lab.state["status"], "completed")
        row = self.lab.history[0]["results"]["mix16"]
        self.assertEqual(len(row["read"]["samples"]), 2)
        self.assertEqual(len(row["write"]["samples"]), 2)
        self.assertEqual(row["read"]["statistics"]["passes"], 2)
        self.assertFalse(self.lab.history[0]["methodology"]["snia_compliance"])
        self.assert_clean()

    def test_sync_engine_rejects_unsupported_queue_depth(self):
        with self.assertRaises(lab_module.AppError):
            self.lab.validate_run({**self.config, "row": "seq8", "engine": "psync"})
        self.assertEqual(self.lab.validate_run({**self.config, "engine": "psync"})["engine"], "psync")

    def test_plot_record_id_cannot_read_arbitrary_paths(self):
        for identifier in ("../../private", "/etc/passwd", "abcd", None):
            with self.assertRaises(lab_module.AppError):
                self.lab.get_plots(identifier)

    def test_cancel_terminates_subprocess_and_cleans_file(self):
        self.fake_tool(delay=15)
        self.lab.start(self.config)
        deadline = time.monotonic() + 3
        while not self.lab.proc and time.monotonic() < deadline:
            time.sleep(.01)
        self.lab.stop()
        self.wait()
        self.assertEqual(self.lab.state["status"], "cancelled")
        self.assertIsNone(self.lab.proc)
        self.assert_clean()

    def test_error_is_not_reported_as_completed(self):
        self.fake_tool(fail=True)
        self.lab.start(self.config)
        self.wait()
        self.assertEqual(self.lab.state["status"], "error")
        self.assertIn("fixture I/O error", self.lab.state["error"])
        self.assert_clean()

    def test_concurrent_run_is_rejected(self):
        self.fake_tool(delay=15)
        self.lab.start(self.config)
        with self.assertRaises(lab_module.AppError):
            self.lab.start(self.config)

    def test_missing_dependency_blocks_start(self):
        self.lab.dependencies["fio"] = {"available": False}
        with self.assertRaises(lab_module.AppError):
            self.lab.start(self.config)
        self.assert_clean()

    def test_regular_file_and_raw_device_cannot_be_targets(self):
        for path in (str(self.sentinel), "/dev", "/dev/null"):
            with self.assertRaises(lab_module.AppError):
                self.lab.validate_run({**self.config, "path": path})

    def test_fio_filename_separator_is_rejected(self):
        bad = self.target / "bad:name"
        bad.mkdir()
        with self.assertRaises(lab_module.AppError):
            self.lab.validate_run({**self.config, "path": str(bad)})

    def test_symlink_to_regular_file_cannot_be_target(self):
        link = self.target / "link"
        link.symlink_to(self.sentinel)
        with self.assertRaises(lab_module.AppError):
            self.lab.validate_run({**self.config, "path": str(link)})

    def test_space_reserve_is_enforced(self):
        usage = type("Usage", (), {"free": 10})()
        with patch.object(lab_module.shutil, "disk_usage", return_value=usage), self.assertRaises(lab_module.AppError):
            self.lab.validate_run(self.config)

    def test_unbounded_parameters_are_rejected(self):
        for config in ({"duration": 999999}, {"size_mib": 0}, {"passes": True}, {"row": "trim"}, {"direct": "1"}):
            with self.assertRaises(lab_module.AppError):
                self.lab.validate_run({**self.config, **config})

    def test_relative_directory_is_rejected(self):
        with self.assertRaises(lab_module.AppError):
            self.lab.validate_run({**self.config, "path": "."})

    def test_smart_requires_scanned_device(self):
        with self.assertRaises(lab_module.AppError):
            self.lab.get_smart("/dev/disk0; rm -rf /")

    def configure_smart(self):
        name = "IOService:/example/AppleANS3CGv2Controller/NS_01@1"
        self.lab.devices = {name: {"name": name, "type": "nvme"}}
        self.lab.dependencies["smartctl"] = {"available": True, "path": "/safe/smartctl"}
        return name

    def test_default_smart_reads_health_without_optional_logs(self):
        name = self.configure_smart()
        raw = {"model_name": "APPLE SSD AP1024Z", "smart_status": {"passed": True}}
        with patch.object(lab_module, "smart_command", return_value=(raw, 0)) as runner, patch.object(lab_module, "apple_nand_info", return_value={"supplier": "Samsung"}) as nand:
            result = self.lab.get_smart(name)
        self.assertEqual(runner.call_args.args[0], ["/safe/smartctl", "-i", "-H", "-A", "-j", "-d", "nvme", name])
        runner.assert_called_once()
        nand.assert_called_once_with(name)
        self.assertEqual(result["error_log"]["status"], "not_requested")

    def test_error_log_failure_keeps_core_health_and_raw_diagnostics(self):
        name = self.configure_smart()
        raw = {"smart_status": {"passed": True}, "nvme_smart_health_information_log": {"critical_warning": 0}}
        log = {"smartctl": {"messages": [{"string": "GetLogPage failed: code=745"}]}}
        with patch.object(lab_module, "smart_command", side_effect=[(raw, 0), (log, 4)]) as runner:
            result = self.lab.get_smart(name, error_log=True)
        self.assertEqual(result["health"], "good")
        self.assertFalse(result["partial"])
        self.assertEqual(result["error_log"]["status"], "failed")
        self.assertEqual(result["error_log"]["raw"], log)
        self.assertEqual(runner.call_args.args[0], ["/safe/smartctl", "-l", "error", "-j", "-d", "nvme", name])

    def test_successful_optional_log_cannot_override_bad_health(self):
        name = self.configure_smart()
        with patch.object(lab_module, "smart_command", side_effect=[({"smart_status": {"passed": False}}, 8), ({}, 0)]):
            result = self.lab.get_smart(name, error_log=True)
        self.assertEqual(result["health"], "bad")

    def test_optional_log_cannot_supply_missing_core_health(self):
        name = self.configure_smart()
        with patch.object(lab_module, "smart_command", side_effect=[({}, 2), ({"smart_status": {"passed": True}}, 0)]):
            result = self.lab.get_smart(name, error_log=True)
        self.assertEqual(result["health"], "unknown")
        self.assertTrue(result["partial"])

    def test_error_log_option_rejects_non_boolean(self):
        with self.assertRaises(lab_module.AppError):
            self.lab.get_smart(self.configure_smart(), error_log="true")

    def test_fiona_is_not_accepted_as_fio(self):
        path = self.root / "not-fio"
        path.write_text(f'#!{sys.executable}\nprint("fio, version 1.10.1")\n')
        path.chmod(0o755)
        self.assertFalse(lab_module.dependency("fio", str(path))["available"])


class AppleNandTests(unittest.TestCase):
    def test_supplier_uses_exact_driver_field_and_preserves_original_name(self):
        value = apple_nand.describe_nand({"vendor-name": b"Toshiba   \0", "nand-marketing-name": "nand-part ", "default-bits-per-cell": 3})
        self.assertEqual(value["supplier"], "Toshiba / Kioxia")
        self.assertEqual(value["reported_name"], "Toshiba")
        self.assertEqual(value["nand_model"], "nand-part")
        self.assertEqual(value["bits_per_cell"], 3)

    def test_vendor_and_manufacturer_id_are_not_guessed(self):
        value = apple_nand.describe_nand({"Vendor Name": "Apple", "manufacturer-id": b"\x98", "vendor-name": "  "})
        self.assertEqual(value["status"], "not_exposed")
        self.assertIsNone(value["supplier"])

    def test_all_requested_suppliers_and_sandisk(self):
        for raw, expected in {"Samsung": "Samsung", "Hynix": "SK hynix", "SKHYNIX": "SK hynix", "Micron": "Micron", "YMTC": "YMTC", "Sandisk": "SanDisk", "New vendor": "New vendor"}.items():
            self.assertEqual(apple_nand.describe_nand({"vendor-name": raw})["supplier"], expected)

    def fake_registry(self):
        def parent(entry, plane, result):
            result._obj.value = 22
            return 0
        def get_class(entry, buffer):
            buffer.value = b"AppleANS3CGv2Controller"
            return 0
        return types.SimpleNamespace(IORegistryEntryFromPath=Mock(return_value=11), IOBSDNameMatching=Mock(return_value=55),
            IOServiceGetMatchingService=Mock(return_value=11), IORegistryEntryGetParentEntry=Mock(side_effect=parent),
            IOObjectGetClass=Mock(side_effect=get_class), IOObjectRelease=Mock())

    def test_io_service_path_resolves_selected_device_and_parent_only(self):
        io = self.fake_registry()
        path = "IOService:/some/AppleANS3CGv2Controller/NS_01@1"
        with patch.object(apple_nand.platform, "system", return_value="Darwin"), patch.object(apple_nand, "_frameworks", return_value=(io, None)), patch.object(apple_nand, "_read_property", side_effect=[None, {"vendor-name": "Micron"}]):
            value = apple_nand.apple_nand_info(path)
        io.IORegistryEntryFromPath.assert_called_once_with(0, path.encode())
        io.IOBSDNameMatching.assert_not_called()
        self.assertEqual([call.args[0] for call in io.IOObjectRelease.call_args_list], [11, 22])
        self.assertEqual(value["supplier"], "Micron")
        self.assertEqual(value["controller"], "AppleANS3CGv2Controller")

    def test_bsd_matching_does_not_choose_another_controller(self):
        io = self.fake_registry()
        with patch.object(apple_nand.platform, "system", return_value="Darwin"), patch.object(apple_nand, "_frameworks", return_value=(io, None)), patch.object(apple_nand, "_read_property", return_value={"vendor-name": "Samsung"}):
            value = apple_nand.apple_nand_info("/dev/rdisk7")
        io.IOBSDNameMatching.assert_called_once_with(0, 0, b"disk7")
        io.IORegistryEntryFromPath.assert_not_called()
        io.IOServiceGetMatchingService.assert_called_once_with(0, 55)
        self.assertEqual(value["supplier"], "Samsung")

    def test_registry_access_failure_is_distinct_from_unreported_vendor(self):
        with patch.object(apple_nand.platform, "system", return_value="Darwin"), patch.object(apple_nand, "_frameworks", side_effect=OSError("unavailable")):
            self.assertEqual(apple_nand.apple_nand_info("/dev/disk0")["status"], "unavailable")
        self.assertEqual(apple_nand.describe_nand({})["status"], "not_exposed")


class PlotLogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.directory.name)
        self.context = {"row": "rnd1", "label": "RND4K Q1T1", "pass": 1, "queue_depth": 1, "threads": 1, "engine": "psync"}

    def tearDown(self):
        self.directory.cleanup()

    def test_per_io_units_offsets_and_direction(self):
        (self.path / "trace_lat.1.log").write_text("1000,100000,0,4096,8192\n1100,40000,1,4096,4096\nbad,line\n")
        result = lab_module.parse_logs(self.path, self.context, "per_io")
        self.assertEqual(result["log_rows"], 2)
        self.assertEqual(result["invalid_rows"], 1)
        self.assertEqual(result["latency_points"][0][:3], [0, 100, 0])
        self.assertEqual(result["latency_points"][0][3], 8192 / 1048576)
        self.assertEqual(result["latency_points"][0][4], 4)
        self.assertEqual(result["latency_points"][1][0], .1)

    def test_window_offsets_are_not_misrepresented_as_individual_io(self):
        (self.path / "trace_lat.1.log").write_text("1000,200000,0,0,0\n")
        (self.path / "trace_bw.1.log").write_text("1000,1024,0,0,0\n")
        (self.path / "trace_iops.1.log").write_text("1000,10000,0,0,0\n")
        result = lab_module.parse_logs(self.path, self.context, "window")
        self.assertIsNone(result["latency_points"][0][3])
        self.assertAlmostEqual(result["flow_points"][0][1], 1.048576)
        self.assertEqual(result["flow_points"][0][2], 10000)

    def test_sampling_preserves_largest_outlier_and_reports_sampling(self):
        rows = [f"{1000+i},{1000+i},0,4096,0\n" for i in range(100)]
        rows.append("1200,999999000,0,4096,0\n")
        (self.path / "trace_lat.1.log").write_text("".join(rows))
        result = lab_module.parse_logs(self.path, self.context, "per_io", budget=5)
        self.assertEqual(max(point[1] for point in result["latency_points"]), 999999)
        self.assertEqual(result["log_rows"], 101)
        self.assertEqual(result["sampling"], "reservoir_plus_slowest_20")

    def test_empty_logs_do_not_generate_points(self):
        result = lab_module.parse_logs(self.path, self.context, "window")
        self.assertEqual(result["latency_points"], [])
        self.assertEqual(result["flow_points"], [])


if __name__ == "__main__":
    unittest.main()
