# Native Enhanced cache post-save probe

This diagnostic branch replaces only the existing manual workflow entry and adds this independent
Java fixture. The original observer-model files are not used by this workflow.

The workflow explicitly selects `gradle/actions/setup-gradle` v6.1.1 at
`5e2ebd065dc2488b7a6ad670704656cbbe1e8f60`, Enhanced caching, Gradle 9.5.0, and the existing
Temurin 21.0.8 selector. The root dependency review describes the original observer model;
it is not a binary-integrity review of this diagnostic configuration.

## One bounded experiment

After separate publication and execution approval, freeze one diagnostic branch commit. Manually
invoke `.github/workflows/model.yml` with `phase=seed`, wait for both jobs and native post-actions
to finish, collect the complete job logs and cache metadata, then invoke the same path/ref/commit
with `phase=consume`. Do not rerun or push another commit between these two invocations.

Each invocation has the same job ID and two fixed matrix contexts (`normal` and `failed`), with
`max-parallel: 1` and `fail-fast: false`. Matrix values do not contain the phase input. Strict
matching prevents cross-job fallback; actual restored keys and sources must still be checked in
native logs. The unique protocol value separates this experiment from earlier contexts.

Seed writes are enabled in both scenarios. The `failed` seed runs three actual tests, fails only
the intentional assertion, uploads its evidence, and exits 1. There is deliberately no
`continue-on-error`, explicit cache/save action, or fixture promotion gate: the pinned action sees
the real failed job and performs its own uncancelled post-action. A failed seed workflow is expected.
The consumer uses read-only caching and makes all three tests pass on unchanged compile inputs.

Both possible failed-seed outcomes are observations: compile outputs may be saved or not saved.
Neither outcome alone is classified as a production defect or a decision about success-only policy.
The normal lane separately asks whether a successful first run supplies reusable compile outputs.

## Evidence and bounds

There are four runner jobs total across two sequential dispatches, peak one job, each capped at
eight minutes: at most 32 runner-minutes, plus queue time. Expected small-fixture execution is
approximately 2-4 minutes/job, not an observed performance claim. Tool downloads, native restore,
cleanup/save, post-action, upload, and failed jobs all count. No reusable workflows or helper jobs
are invoked. Post-actions run after normal artifact upload, so full native post-action logs must
be collected through the Actions API after each job completes; artifacts alone cannot prove save.

The artifacts preserve original exit/timestamp receipts, current-run per-case JUnit markers,
compile/Test task outcomes, pre/post-build task-output manifests, runtime versions, and `/usr/bin/time -v`
output. Its maximum RSS describes the timed process family as reported by that tool, not a simultaneous
aggregate JVM memory peak. Native cache hit/miss and post-save reports require the complete action logs.

Permissions are `contents: read`; no repository secret, environment, release, signing, or publishing
step is requested. The branch workflow is manual-only. The default-branch workflow remains unchanged.
Before publication, recheck all repository triggers and workflow inventory for concurrent changes.

This experiment uses a same-branch `workflow_dispatch` ref, **not** `refs/pull/<N>/merge`. It does not
prove PR-ref visibility, fork behavior, trusted-default-branch isolation, or later-commit fallback.
No PR is needed to answer the initial native post-save question. PR-only claims require separate
authorization and a dedicated public PR-ref experiment, not extrapolation from this branch probe.
