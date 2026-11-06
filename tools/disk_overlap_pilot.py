#!/usr/bin/env python3
"""Bounded same-runner cleanup and archive-restore mechanism experiment."""

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import tarfile
import time
import traceback

MIB = 1024 * 1024
TARGETS = (
    "/usr/local/lib/android",
    "/usr/share/dotnet",
    "/opt/ghc",
    "/usr/local/.ghcup",
)


class Failure(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def interrupted(signum, _frame):
    raise Failure(f"Received signal {signum}", 128 + signum)


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def event(log_path, kind, **fields):
    with log_path.open("a", encoding="utf-8") as output:
        output.write(json.dumps({"event": kind, "monotonic": time.monotonic(),
                                 "pid": os.getpid(), **fields}) + "\n")
        output.flush()


def command(argv, log):
    event(log, "command", argv=argv)
    with log.with_suffix(".stdout").open("a", encoding="utf-8") as output:
        process = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT)
        event(log, "command-started", child_pid=process.pid, pgid=os.getpgrp())
        try:
            result = process.wait()
            if result:
                raise Failure(f"Command exited {result}: {argv}", result)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def content(config):
    size = config["file_bytes"]
    block = random.Random(20261106).randbytes(min(size, 65536))
    return (block * ((size + len(block) - 1) // len(block)))[:size]


def capacity(root, config, initial=False):
    fs = os.statvfs(root)
    free = fs.f_bavail * fs.f_frsize
    inodes = fs.f_favail
    required = config["reserve_bytes"]
    required_inodes = config["reserve_inodes"]
    if initial:
        payload = config["files"] * config["file_bytes"]
        required += payload * (config["passes"] + 1) + payload // 10 + 64 * MIB
        required_inodes += config["files"] * config["passes"] + 128
    if config["fault"] == "space-floor" and initial:
        required = free + 1
    if free < required or inodes < required_inodes:
        raise Failure(f"Capacity barrier: free={free}/{required}, "
                      f"inodes={inodes}/{required_inodes}", 2)
    return {"available_bytes": free, "available_inodes": inodes}


def worker(role, config_path):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    root = Path(config["output"])
    log = root / f"{role}.jsonl"
    save(root / f"{role}.ready.json", {"pid": os.getpid(), "pgid": os.getpgrp()})
    while not (root / f"{role}.go").exists():
        time.sleep(0.01)
    started = time.monotonic()
    event(log, "started", role=role)
    if config["fixture"] and role in ("cleanup", "io"):
        if config["fault"] == "timeout":
            waiting_child = (
                "import json,os,pathlib,signal,sys;"
                "p=pathlib.Path(sys.argv[1]);t=p.with_suffix('.tmp');"
                "t.write_text(json.dumps({'pid':os.getpid(),'pgid':os.getpgrp(),"
                "'ppid':os.getppid()}));t.replace(p);"
                "signal.pause();sys.exit(99)"
            )
            command([sys.executable, "-c", waiting_child,
                     str(root / f"{role}.active.json")], log)
            raise Failure("Timeout fixture unexpectedly released", 99)
        if config["fault"] == f"{role}-fail":
            time.sleep(0.05)
            raise Failure(f"Injected {role} failure", 17 if role == "cleanup" else 19)
        command([sys.executable, "-c", "import time; time.sleep(0.4)"], log)
    if role == "prepare":
        data = content(config)
        archive_path = root / "payload.tar.gz"
        with archive_path.open("xb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb",
                               compresslevel=1, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for number in range(config["files"]):
                        capacity(root, config)
                        item = tarfile.TarInfo(f"file-{number:05d}.bin")
                        item.size = len(data)
                        item.mtime = 0
                        archive.addfile(item, io.BytesIO(data))
            raw.flush()
            os.fsync(raw.fileno())
        payload = config["files"] * config["file_bytes"]
        if (root / "payload.tar.gz").stat().st_size > payload + payload // 10 + MIB:
            raise Failure("Prepared archive exceeded its capacity budget")
        with archive_path.open("rb") as source:
            archive_digest = hashlib.file_digest(source, "sha256").hexdigest()
        event(log, "prepared", archive_sha256=archive_digest,
              archive_bytes=archive_path.stat().st_size,
              file_sha256=hashlib.sha256(data).hexdigest(),
              files=config["files"], file_bytes=config["file_bytes"])
        if config["fixture"]:
            fixture = root / "delete-me"
            fixture.mkdir()
            for number in range(16):
                (fixture / str(number)).write_bytes(b"x" * 4096)
    elif role == "cleanup":
        targets = [str(root / "delete-me")] if config["fixture"] else list(TARGETS)
        for target in targets:
            command(["/usr/bin/rm", "-rf", "--", target], log)
            if os.path.lexists(target):
                raise Failure(f"Cleanup target still exists: {target}")
            event(log, "absent", path=target)
    elif role == "io":
        expected_digest = hashlib.sha256(content(config)).hexdigest()
        written = 0
        for iteration in range(config["passes"]):
            destination = root / f"restored-{iteration}"
            destination.mkdir()
            count = 0
            with tarfile.open(root / "payload.tar.gz", "r:gz") as archive:
                for number, item in enumerate(archive):
                    if (item.name != f"file-{number:05d}.bin" or not item.isfile()
                            or item.size != config["file_bytes"]):
                        raise Failure("Unexpected archive member")
                    capacity(root, config)
                    source = archive.extractfile(item)
                    if source is None:
                        raise Failure("Archive member has no content")
                    target = destination / item.name
                    with source, target.open("xb") as output:
                        while block := source.read(MIB):
                            capacity(root, config)
                            output.write(block)
                            written += len(block)
                        output.flush()
                        os.fsync(output.fileno())
                    with target.open("rb") as restored:
                        actual_digest = hashlib.file_digest(restored, "sha256").hexdigest()
                    if actual_digest != expected_digest:
                        raise Failure(f"Restored content mismatch: {item.name}")
                    count += 1
            if count != config["files"]:
                raise Failure(f"Wrong restored member count: {count}")
            directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        expected = config["files"] * config["file_bytes"] * config["passes"]
        if written != expected:
            raise Failure(f"Wrong restored byte count: {written}/{expected}")
        event(log, "verified", written_bytes=written,
              files=config["files"] * config["passes"], digest=expected_digest)
    elif role == "docker":
        if not config["fixture"]:
            command(["/usr/bin/docker", "info", "--format", "{{.DockerRootDir}}"], log)
            command(["/usr/bin/docker", "image", "ls", "--all", "--no-trunc"], log)
            command(["/usr/bin/docker", "image", "prune", "--all", "--force"], log)
            command(["/usr/bin/docker", "image", "ls", "--all", "--no-trunc"], log)
    else:
        raise Failure(f"Unknown worker role: {role}")
    event(log, "completed", role=role, seconds=time.monotonic() - started)


def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def signal_owned(pgid, signum):
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return


def stop_owned(processes, log):
    for role, process in processes:
        if group_exists(process.pid):
            event(log, "terminate-group", role=role, pgid=process.pid)
            signal_owned(process.pid, signal.SIGTERM)
    for role, process in processes:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if group_exists(process.pid):
                signal_owned(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        if group_exists(process.pid):
            raise Failure(f"Owned process group survived join: {role}/{process.pid}")
        event(log, "joined", role=role, pid=process.pid, exit=process.returncode)


def execute(mode, output, fixture=False, fault="none"):
    os.umask(0o022)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    root = Path(output).resolve()
    root.mkdir(parents=False, exist_ok=False)
    config = {
        "output": str(root), "fixture": fixture, "fault": fault,
        "files": 4 if fixture else 512, "file_bytes": 4096 if fixture else 512 * 1024,
        "passes": 2, "reserve_bytes": 65536 if fixture else 2 * 1024**3,
        "reserve_inodes": 16 if fixture else 2048,
    }
    save(root / "config.json", config)
    log = root / "supervisor.jsonl"
    started = time.monotonic()
    event(log, "started", mode=mode, fixture=fixture)
    deadline = started + (10 if fixture else 420)
    processes = []
    samples = []
    receipts = {}
    phase = "admission"
    status = 1
    error = None
    phase_seconds = {}
    telemetry = (root / "telemetry.jsonl").open("a", encoding="utf-8")

    def check_deadline(boundary):
        if time.monotonic() >= deadline:
            event(log, "deadline-expired", phase=phase, boundary=boundary)
            raise Failure(f"Pilot wall-clock timeout: {boundary}", 124)

    def sample(initial=False):
        if (fault == "sampler-fail" and phase == "critical"
                and (root / "io.go").exists()):
            raise Failure("Injected sampler read failure", 23)
        space = capacity(root, config, initial)
        record = {"monotonic": time.monotonic(), "phase": phase, **space,
                  "cpu_stat": Path("/proc/stat").read_text(),
                  "diskstats": Path("/proc/diskstats").read_text(),
                  "meminfo": Path("/proc/meminfo").read_text()}
        cpu = [int(value) for value in record["cpu_stat"].splitlines()[0].split()[1:9]]
        memory = {line.split(":")[0]: int(line.split()[1]) * 1024
                  for line in record["meminfo"].splitlines()
                  if line.startswith(("MemTotal:", "MemAvailable:"))}
        devices = {}
        for line in record["diskstats"].splitlines():
            fields = line.split()
            devices[fields[2]] = {
                "read_bytes": int(fields[5]) * 512,
                "write_bytes": int(fields[9]) * 512,
                "io_milliseconds": int(fields[12]),
            }
        record.update({"cpu_total_ticks": sum(cpu), "cpu_idle_ticks": cpu[3] + cpu[4],
                       "unavailable_memory_bytes":
                           memory["MemTotal"] - memory["MemAvailable"],
                       "devices": devices})
        telemetry.write(json.dumps(record) + "\n")
        telemetry.flush()
        samples.append({key: record[key] for key in
                        ("monotonic", "available_bytes", "available_inodes",
                         "cpu_total_ticks", "cpu_idle_ticks",
                         "unavailable_memory_bytes", "devices")})

    def run_phase(roles):
        nonlocal deadline
        check_deadline(f"phase entry: {roles}")
        phase_started = time.monotonic()
        next_sample = phase_started
        for role in roles:
            check_deadline(f"spawn: {role}")
            stdout = (root / f"{role}.worker.log").open("w", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "worker", role,
                     str(root / "config.json")], start_new_session=True,
                    stdout=stdout, stderr=subprocess.STDOUT)
            finally:
                stdout.close()
            processes.append((role, process))
            event(log, "spawned", role=role, pid=process.pid, pgid=process.pid)
        for role, process in processes:
            ready = root / f"{role}.ready.json"
            while not ready.exists():
                check_deadline(f"ready: {role}")
                now = time.monotonic()
                if now >= next_sample:
                    sample()
                    next_sample = now + (0.02 if fixture else 1)
                if process.poll() is not None:
                    raise Failure(f"Worker failed before ready: {role}", process.returncode or 1)
                check_deadline(f"ready observation: {role}")
                if time.monotonic() >= phase_started + 5:
                    raise Failure(f"Worker ready timeout: {role}", 124)
                time.sleep(0.01)
            check_deadline(f"ready receipt: {role}")
            identity = json.loads(ready.read_text(encoding="utf-8"))
            if identity != {"pid": process.pid, "pgid": process.pid}:
                raise Failure(f"Worker process-group identity mismatch: {role}")
        for role in roles:
            check_deadline(f"start gate: {role}")
            (root / f"{role}.go").touch(exist_ok=False)
        next_sample = 0
        while True:
            check_deadline(f"phase observation: {roles}")
            now = time.monotonic()
            if now >= next_sample:
                sample()
                next_sample = now + (0.02 if fixture else 1)
            check_deadline(f"after sample: {roles}")
            if fixture and fault == "timeout" and roles == ["cleanup", "io"]:
                active_files = [root / f"{role}.active.json" for role in roles]
                if all(path.exists() for path in active_files):
                    active = {}
                    for role, process in processes:
                        identity = json.loads(
                            (root / f"{role}.active.json").read_text(encoding="utf-8"))
                        if (process.poll() is not None or identity["pgid"] != process.pid
                                or identity["ppid"] != process.pid):
                            raise Failure(f"Timeout fixture is not active: {role}")
                        os.kill(identity["pid"], 0)
                        active[role] = {"worker_pid": process.pid, **identity}
                    event(log, "fixture-timeout-active", phase=phase, workers=active)
                    deadline = time.monotonic()
                    check_deadline("critical active fixture")
            statuses = [(role, process.poll()) for role, process in processes]
            failures = [(role, code) for role, code in statuses if code not in (0, None)]
            if failures:
                code = failures[0][1]
                raise Failure(f"Worker failure: {failures}", code if code > 0 else 128 - code)
            if all(code == 0 for _, code in statuses):
                check_deadline(f"phase success: {roles}")
                break
            time.sleep(0.01 if fixture else 0.1)
        stop_owned(processes, log)
        processes.clear()
        check_deadline(f"phase joined: {roles}")
        for role in roles:
            events = [json.loads(line) for line in
                      (root / f"{role}.jsonl").read_text(encoding="utf-8").splitlines()]
            completed = [item for item in events if item["event"] == "completed"]
            if len(completed) != 1:
                raise Failure(f"Missing unique worker completion receipt: {role}")
            phase_seconds[role] = completed[0]["seconds"]
            receipts[role] = [item for item in events if item["event"] in
                              ("prepared", "verified", "started", "completed")]
        sample()
        check_deadline(f"phase completion: {roles}")

    try:
        if os.stat(root).st_dev != os.stat("/").st_dev:
            raise Failure("Payload and cleanup must share the root filesystem", 2)
        if not fixture:
            for dependency in (root, Path(sys.executable).resolve(),
                               Path(os.__file__).resolve(), Path(tarfile.__file__).resolve()):
                if any(dependency == Path(target) or Path(target) in dependency.parents
                       for target in TARGETS):
                    raise Failure(f"Workload dependency overlaps a cleanup target: {dependency}", 2)
            mounts = Path("/proc/self/mountinfo").read_text()
            targets = []
            for name in TARGETS:
                target = Path(name)
                present = os.path.lexists(target)
                targets.append({"path": name, "present": present,
                                "is_symlink": target.is_symlink(),
                                "is_directory": target.is_dir()})
                if present and target.lstat().st_dev != root.stat().st_dev:
                    raise Failure(f"Cleanup target is on another filesystem: {name}", 2)
                for line in mounts.splitlines():
                    mounted = Path(line.split()[4])
                    if mounted == target or target in mounted.parents:
                        raise Failure(f"Cleanup target contains a mount: {mounted}", 2)
            save(root / "identity.json", {"root_device": root.stat().st_dev,
                                         "targets": targets, "mountinfo": mounts})
            if not any(item["is_directory"] and not item["is_symlink"] for item in targets):
                raise Failure("No real cleanup directory is present", 2)
        sample(initial=True)
        phase = "prepare"
        run_phase(["prepare"])
        phase = "critical"
        sample(initial=True)
        critical_started = time.monotonic()
        if mode == "overlap":
            run_phase(["cleanup", "io"])
        else:
            run_phase(["cleanup"])
            run_phase(["io"])
        critical_seconds = time.monotonic() - critical_started
        check_deadline("critical success barrier")
        event(log, "critical-barrier-passed", mode=mode)
        check_deadline("dependent Docker stage")
        phase = "docker"
        run_phase(["docker"])
        check_deadline("pilot success")
        status = 0
    except Failure as failure:
        error = str(failure)
        status = failure.code
        print(error, file=sys.stderr, flush=True)
    except Exception as failure:
        error = repr(failure)
        status = 1
        traceback.print_exc()
    finally:
        try:
            stop_owned(processes, log)
        except Exception as failure:
            error = f"{error or ''}; join failure: {failure}"
            status = 1
            traceback.print_exc()
        telemetry.close()
        gaps = [b["monotonic"] - a["monotonic"] for a, b in zip(samples, samples[1:])]
        cpu_busy = []
        for previous, current in zip(samples, samples[1:]):
            total = current["cpu_total_ticks"] - previous["cpu_total_ticks"]
            idle = current["cpu_idle_ticks"] - previous["cpu_idle_ticks"]
            if total > 0:
                cpu_busy.append((total - idle) / total)
        io_deltas = {}
        if samples:
            first, last = samples[0]["devices"], samples[-1]["devices"]
            for device in first.keys() & last.keys():
                io_deltas[device] = {key: last[device][key] - first[device][key]
                                     for key in first[device]}
        summary = {
            "schema": 1, "mode": mode, "fixture": fixture, "exit": status, "error": error,
            "failure_phase": phase if status else None,
            "head_sha": os.environ.get("GITHUB_SHA"), "image_os": os.environ.get("ImageOS"),
            "image_version": os.environ.get("ImageVersion"),
            "elapsed_seconds": time.monotonic() - started,
            "critical_seconds": critical_seconds if status == 0 else None,
            "worker_seconds": phase_seconds, "worker_receipts": receipts,
            "cpu_count": os.cpu_count(),
            "sample_count": len(samples),
            "maximum_sample_gap_seconds": max(gaps, default=0),
            "sampled_minimum_available_bytes": min(
                (item["available_bytes"] for item in samples), default=None),
            "sampled_minimum_available_inodes": min(
                (item["available_inodes"] for item in samples), default=None),
            "maximum_interval_cpu_busy_fraction": max(cpu_busy, default=None),
            "sampled_maximum_unavailable_memory_bytes": max(
                (item["unavailable_memory_bytes"] for item in samples), default=None),
            "block_device_io_deltas": io_deltas,
            "before": samples[0] if samples else None,
            "after": samples[-1] if samples else None,
            "owned_groups_joined": all(not group_exists(p.pid) for _, p in processes),
        }
        save(root / "summary.json", summary)
        print(json.dumps(summary), flush=True)
    return status


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, interrupted)
        try:
            worker(sys.argv[2], Path(sys.argv[3]))
            return 0
        except Failure as failure:
            print(str(failure), file=sys.stderr, flush=True)
            return failure.code
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "fixture"))
    parser.add_argument("--mode", choices=("serial", "overlap"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--fault", choices=("none", "cleanup-fail", "io-fail",
                                           "timeout", "sampler-fail", "space-floor"),
                        default="none")
    args = parser.parse_args()
    fixture = args.command == "fixture"
    if fixture and args.fault == "timeout" and args.mode != "overlap":
        parser.error("The active-worker timeout fixture requires overlap mode")
    if not fixture:
        if args.fault != "none":
            parser.error("Fault injection is restricted to owned fixtures")
        if (os.geteuid() != 0 or os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
                or os.environ.get("RUNNER_OS") != "Linux"):
            parser.error("Real cleanup requires an explicitly approved hosted Linux root run")
        missing = [key for key in ("RUNNER_TEMP", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
                                   "GITHUB_SHA", "ImageOS", "ImageVersion")
                   if not os.environ.get(key)]
        if missing:
            parser.error(f"Required hosted-runner identity is missing: {missing}")
        expected = Path(os.environ["RUNNER_TEMP"]) / (
            f"disk-pilot-{os.environ['GITHUB_RUN_ID']}-"
            f"{os.environ['GITHUB_RUN_ATTEMPT']}-{args.mode}")
        if Path(args.output).resolve() != expected.resolve():
            parser.error("Output must be the exact new run/attempt/mode-owned directory")
    return execute(args.mode, args.output, fixture, args.fault)


if __name__ == "__main__":
    sys.exit(main())
