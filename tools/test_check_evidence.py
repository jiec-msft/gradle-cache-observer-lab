import copy
import json
import unittest

from check_evidence import EXPECTED, validate


def fixture():
    lines, xml = [], []
    for identity in sorted(EXPECTED):
        class_name, name = identity.split("#")
        task = ":java-model:test" if class_name == "model.TextTest" else ":kotlin-model:test"
        row = {"run": "fixture-only", "task": task, "caseId": identity + "()"}
        lines += ["MODEL_START " + json.dumps(row), "MODEL_FINISH " + json.dumps({**row, "result": "SUCCESS"})]
        xml.append(f'<testsuite><testcase classname="{class_name}" name="{name}()"/></testsuite>')
    for task in (":java-model:test", ":kotlin-model:test", ":observeFixture"):
        lines.append("MODEL_TASK " + json.dumps({"task": task, "outcome": "EXECUTED", "elapsedNanos": 1}))
    observer = {"mode": "once", "observations": 32, "fingerprintComputations": 1,
                "bytesRead": 131072, "distinctFingerprints": 1, "elapsedNanos": 1}
    return "\n".join(lines), observer, xml


class EvidenceTests(unittest.TestCase):
    def test_all_modes(self):
        log, observer, xml = fixture()
        for mode, work in (("none", (0, 0, 0)), ("once", (1, 131072, 1)), ("repeated", (32, 4194304, 1))):
            with self.subTest(mode=mode):
                updated = {**observer, "mode": mode}
                updated.update(zip(("fingerprintComputations", "bytesRead", "distinctFingerprints"), work))
                self.assertEqual("MODEL_RECEIPTS_VALID", validate(log, updated, "fixture-only", 0, xml)["status"])

    def test_stale_run(self):
        log, observer, xml = fixture()
        with self.assertRaisesRegex(ValueError, "Stale"):
            validate(log, observer, "new-run", 0, xml)

    def test_missing_and_duplicate_case(self):
        log, observer, xml = fixture()
        for bad in ("\n".join(log.splitlines()[1:]), log + "\n" + log.splitlines()[0]):
            with self.assertRaisesRegex(ValueError, "Missing or repeated"):
                validate(bad, observer, "fixture-only", 0, xml)

    def test_cached_test_is_not_execution(self):
        log, observer, xml = fixture()
        with self.assertRaisesRegex(ValueError, "skipped or restored"):
            validate(log.replace('"outcome": "EXECUTED"', '"outcome": "FROM-CACHE"', 1),
                     observer, "fixture-only", 0, xml)

    def test_original_build_failure(self):
        log, observer, xml = fixture()
        with self.assertRaisesRegex(ValueError, "Original Gradle"):
            validate(log, observer, "fixture-only", 7, xml)

    def test_xml_failure_skip_or_missing(self):
        log, observer, xml = fixture()
        for tag in ("failure", "skipped", "error"):
            bad = [xml[0].replace("/>", f"><{tag}/></testcase>"), *xml[1:]]
            with self.assertRaises(ValueError):
                validate(log, observer, "fixture-only", 0, bad)
        with self.assertRaises(ValueError):
            validate(log, observer, "fixture-only", 0, xml[:-1])

    def test_observer_work_must_match(self):
        log, observer, xml = fixture()
        bad = copy.copy(observer)
        bad["bytesRead"] = 0
        with self.assertRaisesRegex(ValueError, "Observer work"):
            validate(log, bad, "fixture-only", 0, xml)

    def test_requested_mode_and_integer_counts(self):
        log, observer, xml = fixture()
        with self.assertRaisesRegex(ValueError, "requested mode"):
            validate(log, observer, "fixture-only", 0, xml, "repeated")
        with self.assertRaisesRegex(ValueError, "Observer work"):
            validate(log, {**observer, "fingerprintComputations": True}, "fixture-only", 0, xml)

    def test_order_and_failed_case(self):
        log, observer, xml = fixture()
        lines = log.splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        with self.assertRaisesRegex(ValueError, "precedes"):
            validate("\n".join(lines), observer, "fixture-only", 0, xml)
        with self.assertRaisesRegex(ValueError, "did not succeed"):
            validate(log.replace('"result": "SUCCESS"', '"result": "FAILURE"', 1),
                     observer, "fixture-only", 0, xml)


if __name__ == "__main__":
    unittest.main()
