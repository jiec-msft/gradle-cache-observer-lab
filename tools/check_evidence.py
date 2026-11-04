#!/usr/bin/env python3
"""Check receipts from this tiny model, not arbitrary test suites."""

import argparse
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

EXPECTED = {
    "model.TextTest#normalizesAscii",
    "model.TextTest#preservesEmptyValue",
    "model.LabelTest#labelsNormalizedText",
    "model.LabelTest#labelsEmptyText",
}


def case_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"model\.\w+#\w+(?:\(\))?", value):
        raise ValueError("Unexpected case identity")
    return value.removesuffix("()")


def validate(log, observer, run_id, build_exit, xml_documents, expected_mode=None):
    if build_exit != 0:
        raise ValueError("Original Gradle build failed: " + str(build_exit))
    starts, finishes, tasks = [], [], {}
    for line in log.splitlines():
        for prefix, destination in (("MODEL_START ", starts), ("MODEL_FINISH ", finishes)):
            if line.startswith(prefix):
                row = json.loads(line[len(prefix):])
                if row["run"] != run_id:
                    raise ValueError("Stale run receipt")
                destination.append(row)
        if line.startswith("MODEL_TASK "):
            row = json.loads(line[len("MODEL_TASK "):])
            if row["task"] in tasks:
                raise ValueError("Repeated task completion")
            tasks[row["task"]] = row
    for rows in (starts, finishes):
        identities = [case_id(row["caseId"]) for row in rows]
        if len(identities) != len(EXPECTED) or set(identities) != EXPECTED:
            raise ValueError("Missing or repeated actual test receipt")
        for row in rows:
            module = "java-model" if row["caseId"].startswith("model.TextTest#") else "kotlin-model"
            if row["task"] != ":" + module + ":test":
                raise ValueError("Test receipt belongs to wrong task")
    if any(row["result"] != "SUCCESS" for row in finishes):
        raise ValueError("A real test did not succeed")
    positions = {}
    for index, line in enumerate(log.splitlines()):
        if line.startswith(("MODEL_START ", "MODEL_FINISH ")):
            prefix, payload = line.split(" ", 1)
            row = json.loads(payload)
            positions[(prefix, case_id(row["caseId"]))] = index
    if any(positions[("MODEL_START", name)] >= positions[("MODEL_FINISH", name)] for name in EXPECTED):
        raise ValueError("Test finish precedes start")
    for name in (":java-model:test", ":kotlin-model:test", ":observeFixture"):
        if tasks.get(name, {}).get("outcome") != "EXECUTED":
            raise ValueError("Required task was skipped or restored: " + name)
    if any(type(row.get("elapsedNanos")) is not int or row["elapsedNanos"] < 0 for row in tasks.values()):
        raise ValueError("Malformed task timing")
    cases = []
    for document in xml_documents:
        root = ET.fromstring(document)
        for node in root.iter("testcase"):
            if any(node.find(tag) is not None for tag in ("failure", "error", "skipped")):
                raise ValueError("JUnit XML contains failure or skip")
            cases.append(case_id(node.attrib["classname"] + "#" + node.attrib["name"]))
    if len(cases) != len(EXPECTED) or set(cases) != EXPECTED:
        raise ValueError("JUnit XML case set differs from actual receipts")
    work = {"none": (0, 0, 0), "once": (1, 131072, 1), "repeated": (32, 4194304, 1)}
    mode = observer.get("mode")
    if mode not in work or observer.get("observations") != 32:
        raise ValueError("Unexpected observer protocol")
    if expected_mode is not None and mode != expected_mode:
        raise ValueError("Actual observer mode differs from requested mode")
    actual = tuple(observer.get(key) for key in ("fingerprintComputations", "bytesRead", "distinctFingerprints"))
    if any(type(value) is not int for value in actual) or actual != work[mode]:
        raise ValueError("Observer work differs from immutable-fixture protocol")
    if type(observer.get("elapsedNanos")) is not int or observer["elapsedNanos"] < 0:
        raise ValueError("Malformed observer timing")
    return {"status": "MODEL_RECEIPTS_VALID", "run": run_id, "executed_cases": sorted(cases),
            "tasks": tasks, "observer": observer, "gradle_exit": build_exit,
            "boundary": "Synthetic mechanism evidence only, not product performance"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=("none", "once", "repeated"), required=True)
    parser.add_argument("--build-exit", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        documents = [path.read_text(encoding="utf-8") for module in ("java-model", "kotlin-model")
                     for path in sorted(Path(module, "build", "test-results", "test").glob("TEST-*.xml"))]
        receipt = validate(args.log.read_text(encoding="utf-8"), json.loads(args.observer.read_text(encoding="utf-8")),
                           args.run_id, args.build_exit, documents, args.mode)
        code = 0
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as error:
        receipt = {"status": "INVALID_OR_INCOMPLETE", "gradle_exit": args.build_exit,
                   "error": type(error).__name__ + ": " + str(error)}
        code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(receipt, indent=2) + "\n")
    if code:
        print(receipt["error"], file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
