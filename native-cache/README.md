# Same-PR native cache follow-up probe

This temporary diagnostic PR is not intended to merge. It uses the same small Java fixture,
Gradle 9.5.0, Temurin 21.0.8, and pinned setup-gradle v6.1.1 Enhanced provider. All three
JUnit cases execute on every revision; Test results are never reused.

The workflow path remains `.github/workflows/model.yml`. Only `pull_request` opened and
synchronize events against main are enabled on this branch. There is one fixed matrix cell:
job `model`, scenario `normal`, protocol `native-post-save-20261106-r1`.
Owner-authored, same-repository PRs from `native-pr-followup-20261106` are the only admitted
source. Attempts beyond the first and PRs with more than three commits are excluded.
There is no push, dispatch, PR-target, follower, reusable workflow, or helper job here.

## At most three owner-controlled revisions

1. Publish one initial commit from the exact reviewed main baseline, then open one draft PR.
   The initial successful tests and complete native post-save form the required seed.
2. Only after that job and its complete post-action are terminal, change `value * 2` to
   `value + value` in `SampleMath.twice`. This changes implementation bytecode but not the
   public ABI. Expect main compilation to execute; observe whether test compilation reuses
   its cache rather than assuming it must.
3. Only if still necessary and separately admitted, wait for the second complete post-action,
   then make one documentation-only change. No compiler input changes. Observe actual
   compile cache outcomes and which previous PR entry supplied the restore.

Each event has at most one eight-minute job, so the complete cap is three automatic runs
and 24 configured runner-minutes, peak one job. Opening a draft PR still starts its job.
Do not push another revision while its predecessor's post-action is active. No rerun,
extra revision, automatic code push, or default-branch merge belongs to this experiment.
Two revisions suffice if they settle the question; the third is not automatic.

## Keep identities and evidence separate

Checkout intentionally uses the event default, not the PR head override. Context records
the PR number/head/base, `GITHUB_SHA`, actual checkout SHA, raw commit parent headers,
workflow SHA, and `refs/pull/<number>/merge`. Raw commit headers work with shallow checkout.
An event's optional `merge_commit_sha` can be unknown; it is not substituted for observation.

After each revision, collect the full native post logs and cache metadata. Confirm owned
primary writes actually use this PR merge-ref, not a head-branch or default-branch ref.
On the next head, use the lower-level **Cache hit for** matched key plus native restore
logs, API identity candidates, and raw precompile entry hashes to identify the supplier.
Do not call the newly requested key the supplier merely because it contains the new SHA.
Record whether native key components refer to the head or checkout; do not assume equality.
Shared dependency/split-leaf producer attribution stays unknown unless independently proved.

Compile `FROM-CACHE` and precompile entry restoration are separate observations. Compare
the required compile-entry blobs with the previous successful revision's after-build
manifest. Native successful cleanup may remove obsolete unused entries, so whole-manifest
equality after an implementation change is not required. Record all differences.

Every job retains original exit/timestamp receipts, current-run per-case JUnit markers,
actual task outcomes, pre/post-build task-output manifests, runtime versions and command
resource output. Normal artifact upload precedes native post-save, so artifacts alone
cannot attest to post-save. Count setup, restore, provision, build, post and complete jobs.

## Trust and policy boundaries

Cache writes are enabled only inside this diagnostic PR workflow. The default-branch
workflow and its code are not modified or merged. Permissions remain `contents: read`;
no repository secret or privileged PR-target execution is used.

The first successful seed is an orchestration gate, not a claim that `cache-cleanup:
on-success` prevents native saves after a failed Test. Unexpected test/source/tooling
failure stops later revisions; do not repair and retry inside the same execution budget.
This same-repository experiment does not establish fork or trusted-main isolation, nor
production latency, coverage, or stability. No production project is exercised.
