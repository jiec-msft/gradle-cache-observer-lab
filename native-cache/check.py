#!/usr/bin/env python3
"""Collect this fixed public fixture's evidence, not arbitrary build receipts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import xml.etree.ElementTree as ET

EXPECTED_CASES = {"doublesPositive", "doublesZero", "checksFailureBoundary"}


def snapshot(home):
    directory = Path(home, "caches", "build-cache-1")
    return {
        "directory_exists": directory.is_dir(),
        "entries": {
            entry.name: {
                "bytes": entry.stat().st_size,
                "sha256": hashlib.sha256(entry.read_bytes()).hexdigest(),
            }
            for entry in sorted(directory.glob("*"))
            if entry.is_file() and re.fullmatch(r"[a-f0-9]{32}", entry.name)
        },
        "boundary": "Local entries before native post-save; not proof of remote upload.",
    }


def validate(evidence, project, run_id, phase, scenario):
    if phase not in {"seed", "consume"} or scenario not in {"normal", "failed"}:
        raise ValueError("Unexpected phase or scenario")
    execution = json.loads((evidence / "execution.json").read_text(encoding="utf-8"))
    expected_failure = phase == "seed" and scenario == "failed"
    expected_exit = 1 if expected_failure else 0
    if execution["gradle_exit"] != expected_exit or execution["log_exit"] != 0:
        raise ValueError("Original Gradle or log-preservation exit differs from the expected probe")
    log = (evidence / "gradle.log").read_text(encoding="utf-8")
    tasks = {}
    for line in log.splitlines():
        if line.startswith("NATIVE_TASK "):
            task = json.loads(line.removeprefix("NATIVE_TASK "))
            if task["task"] in tasks:
                raise ValueError("Repeated task completion")
            tasks[task["task"]] = task["outcome"]
    for name in (":compileJava", ":compileTestJava"):
        if tasks.get(name) not in {"EXECUTED", "FROM-CACHE"}:
            raise ValueError("Compile task missing or unexpectedly skipped: " + name)
    if tasks.get(":test") != ("FAILED" if expected_failure else "EXECUTED"):
        raise ValueError("Test was missing, skipped, restored, or failed unexpectedly")
    if "'Every native probe must execute real tests' satisfied" not in log:
        raise ValueError("Native log does not confirm disabled Test result caching")
    if "Task.upToDateWhen is false" not in log:
        raise ValueError("Native log does not confirm forced Test execution")
    documents = sorted((project / "build" / "test-results" / "test").glob("TEST-*.xml"))
    if len(documents) != 1:
        raise ValueError("Expected exactly one current JUnit document")
    root = ET.fromstring(documents[0].read_text(encoding="utf-8"))
    cases = root.findall("testcase")
    if len(cases) != 3 or {case.attrib["name"].removesuffix("()") for case in cases} != EXPECTED_CASES:
        raise ValueError("Current JUnit case set differs from this fixture")
    if int(root.attrib["tests"]) != 3 or int(root.attrib["failures"]) != int(expected_failure):
        raise ValueError("Current JUnit totals differ from the expected probe")
    if int(root.attrib["errors"]) or int(root.attrib["skipped"]):
        raise ValueError("JUnit contains errors or skipped cases")
    failures = []
    for case in cases:
        if case.attrib["classname"] != "model.SampleMathTest":
            raise ValueError("Unexpected JUnit test class")
        marker = case.findtext("system-out")
        if marker is None or ("NATIVE_CASE run=" + run_id) not in marker.splitlines():
            raise ValueError("JUnit case has no current-run marker")
        if case.find("error") is not None or case.find("skipped") is not None:
            raise ValueError("JUnit case has an error or skip")
        for failure in case.findall("failure"):
            failures.append((case.attrib["name"].removesuffix("()"), failure.attrib))
    if expected_failure:
        if len(failures) != 1 or failures[0][0] != "checksFailureBoundary":
            raise ValueError("Failure did not belong to the intentional boundary case")
        details = failures[0][1]
        if details["type"] != "java.lang.AssertionError" or not re.fullmatch(
            r"(?:java\.lang\.AssertionError: )?Intentional native failure probe", details["message"]
        ):
            raise ValueError("Failure was not exactly the intentional assertion")
    elif failures:
        raise ValueError("Unexpected JUnit failure")
    return {
        "status": "BUILD_EVIDENCE_VALID",
        "phase": phase,
        "scenario": scenario,
        "run_id": run_id,
        "gradle_exit": execution["gradle_exit"],
        "executed_cases": sorted(EXPECTED_CASES),
        "intentional_failures": int(expected_failure),
        "tasks": tasks,
        "compile_reused": all(tasks[name] == "FROM-CACHE" for name in (":compileJava", ":compileTestJava")),
        "boundary": "Observe native post-action logs separately. No success-only promotion is implemented here.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("context", "snapshot", "validate"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == "context":
            record = {
                name: os.environ[name]
                for name in ("NATIVE_PHASE", "NATIVE_SCENARIO", "NATIVE_RUN_ID", "GITHUB_REF", "GITHUB_SHA",
                             "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
            }
            if record["GITHUB_EVENT_NAME"] != "workflow_dispatch":
                raise ValueError("This candidate only measures same-branch manual invocations")
            record["python"] = platform.python_version()
        elif args.mode == "snapshot":
            record = snapshot(os.environ["GRADLE_USER_HOME"])
        else:
            record = validate(Path("evidence"), Path("native-cache"), os.environ["NATIVE_RUN_ID"],
                              os.environ["NATIVE_PHASE"], os.environ["NATIVE_SCENARIO"])
    except (OSError, KeyError, ValueError, TypeError, ET.ParseError) as error:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({"status": "INVALID_OR_INCOMPLETE", "error": str(error)}, stream, indent=2)
            stream.write("\n")
        print(str(error), file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
