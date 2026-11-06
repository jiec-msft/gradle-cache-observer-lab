#!/usr/bin/env python3
"""Small owned Linux fixtures; never invokes hosted-runner cleanup or Docker."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).with_name("disk_overlap_pilot.py")


class LifecycleTest(unittest.TestCase):
    def run_case(self, mode="overlap", fault="none", cancel=None):
        with tempfile.TemporaryDirectory(prefix="disk-pilot-check-") as directory:
            root = Path(directory) / "result"
            process = subprocess.Popen(
                [sys.executable, str(SCRIPT), "fixture", "--mode", mode,
                 "--output", str(root), "--fault", fault],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                start_new_session=True)
            try:
                if cancel:
                    until = time.monotonic() + 5
                    while not (root / "io.go").exists():
                        if process.poll() is not None or time.monotonic() > until:
                            self.fail("Fixture never reached the overlap stage")
                        time.sleep(0.01)
                    time.sleep(0.05)
                    process.send_signal(cancel)
                stdout, _ = process.communicate(timeout=10)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
            summary = json.loads((root / "summary.json").read_text())
            print(json.dumps({"mode": mode, "fault": fault, "cancel": cancel,
                              "exit": process.returncode, "error": summary["error"],
                              "elapsed_seconds": summary["elapsed_seconds"],
                              "critical_seconds": summary["critical_seconds"],
                              "sample_count": summary["sample_count"],
                              "maximum_sample_gap_seconds":
                                  summary["maximum_sample_gap_seconds"],
                              "owned_groups_joined": summary["owned_groups_joined"]}),
                  flush=True)
            events = [json.loads(line) for line in
                      (root / "supervisor.jsonl").read_text().splitlines()]
            for item in events:
                if item["event"] == "spawned":
                    with self.assertRaises(ProcessLookupError):
                        os.killpg(item["pgid"], 0)
            barrier = any(e["event"] == "critical-barrier-passed" for e in events)
            self.assertEqual(barrier, process.returncode == 0, stdout)
            self.assertTrue(summary["owned_groups_joined"], stdout)
            if process.returncode:
                self.assertFalse(any(e.get("role") == "docker" for e in events), stdout)
                sibling = {"cleanup-fail": "io", "io-fail": "cleanup"}.get(fault)
                if sibling:
                    sibling_events = [json.loads(line) for line in
                                      (root / f"{sibling}.jsonl").read_text().splitlines()]
                    self.assertFalse(any(e["event"] == "completed" for e in sibling_events))
                if fault == "timeout":
                    self.assertTrue((root / "cleanup.go").exists())
                    self.assertTrue((root / "io.go").exists())
                    self.assertEqual(summary["failure_phase"], "critical")
                    self.assertEqual(summary["error"],
                                     "Pilot wall-clock timeout: critical active fixture")
                    active = [e for e in events if e["event"] == "fixture-timeout-active"]
                    self.assertEqual(len(active), 1)
                    self.assertEqual(set(active[0]["workers"]), {"cleanup", "io"})
                    for role, identity in active[0]["workers"].items():
                        self.assertEqual(identity["pgid"], identity["worker_pid"])
                        self.assertEqual(identity["ppid"], identity["worker_pid"])
                        with self.assertRaises(ProcessLookupError):
                            os.kill(identity["pid"], 0)
                        with self.assertRaises(ProcessLookupError):
                            os.kill(identity["worker_pid"], 0)
                        role_events = [json.loads(line) for line in
                                       (root / f"{role}.jsonl").read_text().splitlines()]
                        self.assertTrue(any(e["event"] == "started" for e in role_events))
                        self.assertFalse(any(e["event"] == "completed" for e in role_events))
                    print(json.dumps({"kind": "critical-timeout-reaping-proof",
                                      "active": active[0],
                                      "failure_phase": summary["failure_phase"],
                                      "critical_barrier": barrier, "docker_spawned": False,
                                      "workers_and_children_reaped": True}), flush=True)
            if process.returncode == 0:
                self.assertGreater(summary["sample_count"], 2)
                timings = {}
                for role in ("cleanup", "io"):
                    timings[role] = [json.loads(line) for line in
                                     (root / f"{role}.jsonl").read_text().splitlines()]
                cleanup_start = next(e["monotonic"] for e in timings["cleanup"]
                                     if e["event"] == "started")
                cleanup_end = next(e["monotonic"] for e in timings["cleanup"]
                                   if e["event"] == "completed")
                io_start = next(e["monotonic"] for e in timings["io"]
                                if e["event"] == "started")
                io_end = next(e["monotonic"] for e in timings["io"]
                              if e["event"] == "completed")
                if mode == "serial":
                    self.assertLessEqual(cleanup_end, io_start)
                else:
                    self.assertLess(max(cleanup_start, io_start), min(cleanup_end, io_end))
                verified = next(e for e in timings["io"] if e["event"] == "verified")
                self.assertEqual(verified["files"], 8)
                self.assertEqual(verified["written_bytes"], 32768)
            return process.returncode, summary

    def test_serial(self):
        self.assertEqual(self.run_case(mode="serial")[0], 0)

    def test_overlap(self):
        self.assertEqual(self.run_case()[0], 0)

    def test_cleanup_failure_stops_io(self):
        self.assertEqual(self.run_case(fault="cleanup-fail")[0], 17)

    def test_io_failure_stops_cleanup(self):
        self.assertEqual(self.run_case(fault="io-fail")[0], 19)

    def test_timeout(self):
        self.assertEqual(self.run_case(fault="timeout")[0], 124)

    def test_cancel(self):
        self.assertEqual(self.run_case(cancel=signal.SIGTERM)[0], 143)

    def test_interrupt(self):
        self.assertEqual(self.run_case(cancel=signal.SIGINT)[0], 130)

    def test_sampler_failure(self):
        self.assertEqual(self.run_case(fault="sampler-fail")[0], 23)

    def test_initial_space_barrier(self):
        code, summary = self.run_case(fault="space-floor")
        self.assertEqual(code, 2)
        self.assertEqual(summary["sample_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
