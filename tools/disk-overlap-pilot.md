# Same-runner disk-overlap mechanism pilot

This is a generic mechanism experiment, not an application benchmark.
Each matrix arm runs the complete workload on its own hosted runner. Within the
overlap arm, directory cleanup and archive restore run on the **same root
filesystem**. The serial and overlap arms are not the same physical machine;
compare their recorded image versions and report that uncontrolled difference.

## Source and deliberate semantic differences

The independently authored Python adapter references the enabled operation scope
of [free-disk-space at 54081f138730dfa15788a46383842cd2f914a1be](https://github.com/lumbroso-commons/free-disk-space/blob/54081f138730dfa15788a46383842cd2f914a1be/action.yml),
whose upstream license is MIT, copyright 2022 Jeremie Lumbroso
([pinned license](https://github.com/lumbroso-commons/free-disk-space/blob/54081f138730dfa15788a46383842cd2f914a1be/LICENSE)).
No upstream Bash implementation is copied.
It is **not** a background invocation of a composite `uses:` action.

Both arms use this exact same adapter and workload:

1. Prepare and fsync a deterministic approximately 256 MiB gzip tar, containing
   512 files of 512 KiB. Preparation is outside the critical comparison, but
   inside whole-pilot time and capacity sampling.
2. Delete only the four fixed Android/.NET/Haskell directories. Serial then
   restores the archive; overlap runs those two stages concurrently. Restoration
   creates two independent output trees, fsyncs every file and output directory,
   and verifies every file's SHA-256, total file count and 512 MiB written.
3. Join both stages successfully before running Docker inspection/prune.
   Docker state is **never** overlapped with restoration.

Compared with the referenced action, deletion/prune errors now fail visibly
instead of `|| true`, absence is asserted, Docker is moved after the joint
barrier, and all workers run as root so one owner can signal and join their
process groups without nested sudo sessions. Both arms share these changes.
The disabled apt/tool-cache/swap operations are not implemented. The original
action's per-operation `df` macros are replaced with independent sampling.
Results are not directly comparable with an earlier unadapted-action run.

## Capacity, ownership and measurement

Before preparation and again before either critical arm, require space for the
entire bounded archive plus both output trees, 10% archive expansion, 64 MiB
evidence allowance and a 2 GiB reserve. Require space for all output inodes plus
128 metadata entries and 2048 reserved inodes. Workers recheck reserve before
each file/chunk; this is a finite generic payload bound, not a production-cache
capacity estimate. No previously observed free-space value is an admission rule.
The fixed payload requires 3,046,742,425 bytes (about 2.838 GiB) and 3200 free
inodes at admission; the conservative full bound is checked again after archive
preparation, even though its archive allocation has already occurred.

The supervisor samples `statvfs`, `/proc/stat`, `/proc/diskstats` and
`/proc/meminfo` every approximately one second, including preparation and joins.
Raw counters permit CPU/IO deltas; block-device counters are system-wide, not
attributable per-path IO. Record sampled minimum space/inodes and maximum sample
gap, **not an absolute continuous minimum** or unsampled resource peak.
The summary includes interval CPU busy fractions, sampled unavailable-memory
maximum and unsummed per-block-device IO deltas, with full raw samples retained.
Check that output and root have the same filesystem device before starting.
Present cleanup targets must also be on that device, with no mounts at or
beneath the deletion targets; require at least one real target directory.

Each worker starts in a new session and reports PID/PGID before its start gate.
The supervisor owns every group, waits for all successful exits, and propagates
worker errors, monitor errors, low-space conditions, timeout, SIGINT and SIGTERM.
On failure it signals owned groups, escalates only those groups if necessary,
joins them, and checks that they no longer exist. The script has a 420-second
wall limit inside an 8-minute step and 10-minute job. SIGKILL, host loss, sudo's
runner-level signal forwarding and Docker daemon rollback are not proven by a
local fixture; daemon work cannot be rolled back by killing its client.

The workflow has exactly two matrix jobs, zero helper jobs and no Java, Gradle,
test suite, cache action or publishing step. Each job is capped at ten minutes:
at most twenty runner-minutes. It only responds to manual dispatch and never
modifies repository refs. Evidence uploads are best effort after failure or
cancellation; hard job termination may prevent upload.
Fresh deadline checks precede phase entry, each worker spawn and start gate,
phase success/join completion, the critical success barrier, dependent Docker
work and final success. Sampling does not reuse a pre-sample clock reading.

## Offline fixture and pilot acceptance

On Linux, `python3 tools/test_disk_overlap_pilot.py` exercises this supervisor
with tiny owned fixtures, not real system cleanup or Docker. It covers serial
and overlapping intervals, exact restored bytes, both worker failures, timeout,
SIGINT/SIGTERM, monitor failure and initial capacity rejection, and checks all spawned
groups have been reaped. This is not a real hosted-runner cancellation receipt.
The timeout fixture does not race a sleep against a short deadline. Each
critical worker starts a child that atomically publishes its PID/PGID/parent
identity and blocks in `signal.pause()`. Only after both active identities are
verified does the fixture expire the supervisor deadline. It then requires
exit 124 in the critical phase, no successful barrier or Docker spawn, and
reaped workers plus both identified children. The real-run deadline is unchanged.

Accept a mechanism pilot only if both complete arms have the expected source,
matching image version/CPU count and prepared-archive digest, correct payload
counts/digests, successful joins, at least three samples with no gap over 2.5
seconds, no capacity violations, and intersecting actual worker intervals in
the overlap arm (with non-intersecting intervals in the serial arm).
Report preparation, critical interval, Docker, whole job, all runner-minutes,
all failures and sampled resource values. A single pair cannot establish stable
performance gains. Cached archive reads, per-file fsync, no network download and
no application/toolchain work limit how representative this IO proxy is.
