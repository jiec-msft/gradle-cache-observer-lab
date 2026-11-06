"""Synthetic validator unit tests; these do not execute Gradle or prove native cache behavior."""

import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from check import snapshot, validate


class EvidenceChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.project = self.root / "project"
        self.xml_directory = self.project / "build" / "test-results" / "test"
        self.xml_directory.mkdir(parents=True)

    def prepare(self, failed=False):
        (self.evidence / "execution.json").write_text(
            json.dumps({"gradle_exit": int(failed), "log_exit": 0}), encoding="utf-8"
        )
        tasks = {":compileJava": "FROM-CACHE", ":compileTestJava": "FROM-CACHE",
                 ":test": "FAILED" if failed else "EXECUTED"}
        lines = ["NATIVE_TASK " + json.dumps({"task": name, "outcome": outcome})
                 for name, outcome in tasks.items()]
        lines += ["'Every native probe must execute real tests' satisfied", "Task.upToDateWhen is false"]
        (self.evidence / "gradle.log").write_text("\n".join(lines), encoding="utf-8")
        root = ET.Element("testsuite", tests="3", failures=str(int(failed)), errors="0", skipped="0")
        for name in ("doublesPositive", "doublesZero", "checksFailureBoundary"):
            case = ET.SubElement(root, "testcase", classname="model.SampleMathTest", name=name)
            ET.SubElement(case, "system-out").text = "NATIVE_CASE run=fixture-1\n"
            if failed and name == "checksFailureBoundary":
                ET.SubElement(case, "failure", type="java.lang.AssertionError",
                              message="java.lang.AssertionError: Intentional native failure probe")
        self.xml_path = self.xml_directory / "TEST-model.SampleMathTest.xml"
        ET.ElementTree(root).write(self.xml_path, encoding="utf-8")

    def check(self, phase="consume", scenario="normal"):
        return validate(self.evidence, self.project, "fixture-1", phase, scenario)

    def test_normal_consumer_reports_observed_reuse(self):
        self.prepare()
        self.assertTrue(self.check()["compile_reused"])

    def test_expected_failed_seed_is_valid_evidence_not_a_successful_build(self):
        self.prepare(failed=True)
        result = self.check("seed", "failed")
        self.assertEqual(result["gradle_exit"], 1)
        self.assertEqual(result["intentional_failures"], 1)

    def test_unexpected_failure_does_not_count_as_expected(self):
        self.prepare(failed=True)
        with self.assertRaises(ValueError):
            self.check("seed", "normal")

    def test_run_identifier_requires_an_exact_line(self):
        self.prepare()
        self.xml_path.write_text(self.xml_path.read_text(encoding="utf-8").replace("fixture-1", "fixture-10"),
                                 encoding="utf-8")
        with self.assertRaises(ValueError):
            self.check()

    def test_wrong_failure_message_is_rejected(self):
        self.prepare(failed=True)
        self.xml_path.write_text(self.xml_path.read_text(encoding="utf-8").replace(
            "Intentional native failure probe", "Other failure"), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.check("seed", "failed")

    def test_restored_test_results_are_rejected(self):
        self.prepare()
        path = self.evidence / "gradle.log"
        path.write_text(path.read_text(encoding="utf-8").replace('"EXECUTED"', '"FROM-CACHE"'), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.check()

    def test_invalid_scenario_is_rejected(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.check(scenario="unknown")

    def test_missing_cache_directory_is_explicit(self):
        result = snapshot(self.root / "absent-home")
        self.assertFalse(result["directory_exists"])
        self.assertEqual(result["entries"], {})


if __name__ == "__main__":
    unittest.main()
