# Comparative benchmark

This harness tests whether Spawn completes more original tasks within matched
cost/time limits, or improves cost/time at comparable quality. It does not assume
Spawn wins. The bundled suite is **authored development smoke data**, not a
representative benchmark or a replacement for the preserved v1 pilot.

## Offline walkthrough

From the repository root, after the development install in CONTRIBUTING.md:

```sh
python benchmarks/compare.py prepare \
  --suite benchmarks/suite.example.json \
  --models src/spawn/templates/models.toml --model fast \
  --repetitions 1 --output .spawn/benchmark-smoke
python benchmarks/compare.py run --output .spawn/benchmark-smoke
python benchmarks/compare.py export-review --output .spawn/benchmark-smoke \
  --packet .spawn/review-smoke.json
python benchmarks/compare.py summarize --output .spawn/benchmark-smoke
```

Default execution makes **no network or model calls**. It returns empty synthetic
findings to exercise planning, artifacts and scoring. Synthetic runs never receive
a primary success rate or performance comparisons, even if somebody grades them
successful. Template prices are placeholders, not measurements.

Each output directory is a new, non-resumable experiment. Preparation snapshots
sources and freezes profiles, rubrics, conditions, implementation hashes and the
randomized schedule. Edits invalidate the frozen input. Execution refuses a second
start; preserve interrupted runs and prepare a new, separately identified study.
Analysis of an old study does not require the old implementation to be installed.
The source/data hashes are local integrity checks, not independent preregistration.
An incomplete execution exits 3; input/setup errors exit 2. Neither permits silently
discarding the records or restarting in the same directory.

## Tasks and constraints

Copy `suite.example.json` and supply independently sourced, legally reusable tasks.
Source paths resolve relative to the suite. Only named local text files are read;
the normal UTF-8 ingestion limits apply. Each task declares:

| Field | Meaning |
| --- | --- |
| `id`, `family` | Unique task ID and task family, e.g. extraction or conflicting evidence. |
| `group` | Related tasks/documents sharing a sampling unit. Bootstrap this unit, not repetitions. |
| `split` | `development` or `test`; do not tune on test tasks or outcomes. |
| `origin`, `provenance` | `authored` or `external`, source attribution, licensing and construction history. |
| `goal`, `sources`, `criteria` | The original execution contract. Higher integer priority means more important. |
| `rubric` | Human-review guidance for every criterion, including correct abstention. Never sent to models. |

Related groups cannot cross splits; identical sources must share a group.
These checks do
not detect paraphrases, shared underlying documents or contamination in model
training. Declaring a split `test` or an origin `external` does not prove independence.
Recruit independent task authors/reviewers, keep task families separate, document
licensing, and choose sample size from development-set variance and the minimum
effect of interest before freezing the test study.

Include extraction, synthesis, conflicting evidence, unsupported questions and
long-document tasks. Do not select only cases favoring adaptation. Conditions give
explicit `max_usd` and `max_seconds`: choose generous, moderate and tight values
on development data. Optional `change` has `after_seconds`, `max_usd` and a shorter
`max_seconds`. These are absolute totals from run start. A wall-clock timer applies
the update atomically; runtime observes it at call boundaries. Record actual update
time. Finishing before the change is legitimate; an in-flight call may exceed it.

## Policies and controlled comparisons

All arms use the same runtime, evidence instructions, excerpt selector, permission
boundaries, model profiles and limits. Comparisons therefore isolate scheduling
and routing within Spawn's execution stack, not superiority over other frameworks.
History-based forecasts are disabled to avoid learning across evaluation arms.

- **Spawn:** the actual heuristic planner and adaptive runtime.
- **Fixed single pass:** all original criteria in one call; refuses if that call
  does not fit. No automatic fallback.
- **Fixed per criterion:** priority-ordered one-criterion calls. Stops when the next
  criterion does not fit; it does not demand that all future calls fit upfront.
- **Budget-aware batching:** greedily fit the largest priority prefix in a single
  batch, choosing among approved models by coverage, upper cost, upper time and name.
  Replans remaining work between calls. It does not search incremental strategies.

`--experiment scheduling --model NAME` pins the same profile for every arm.
`--experiment selection` gives Spawn and budget-aware batching the same complete
pool and runs each fixed policy separately for **every** profile. Report all these
baselines, not just the weakest model or a retrospectively chosen winner.

Order is randomized within matched task/condition/repetition blocks, with shuffled
block order and a recorded seed. Repeat runs; do not treat repetitions as independent
tasks. Provider cache state, load, model revisions and hidden SDK retries remain
potential confounders. Application-level attempts and complete returned billing
are recorded; internal provider calls are not independently observed. Python,
OS, architecture and installed package versions are saved. Also record the installed
Copilot CLI version and provider/model revision information for a live publication.

## Live execution: separate approval required

Replace example models/prices/throughput and establish profiles on separate
development runs. Review frozen `plan.json`, all sources/models and the printed
sum of per-run budgets before authorizing a study:

```sh
python benchmarks/compare.py run --output PATH_TO_FROZEN_EXPERIMENT \
  --live --approve-sharing --max-total-usd APPROVED_LIMIT --max-calls APPROVED_COUNT
```

Those flags authorize sending the frozen source excerpts to the frozen model
endpoints and writing sensitive responses/artifacts locally. No credentials are
stored; provider adapters use the configured environment variables or CLI sign-in.
Any prior pilot authorization does **not** authorize this study.

The aggregate gate reserves each call's upper cost before starting it. Incomplete
billing, failures, interruptions, reservation overruns or exhausted study caps
halt further paid calls; failed calls retain reservations. No automatic reruns.
Missing cases remain visibly unexecuted, not silently discarded. Provider caps
and timeouts are best-effort, not spending guarantees. Copilot requires at least
$0.30 per-call reservation and reports allowance-inclusive usage equivalents,
not invoice charges. Do not mix those units with direct-API prices in one study.

Timing includes source loading, planning/replanning, CLI/SDK startup, calls,
evidence checks, receipts, history writes and deadline-controller teardown.
Preparation/snapshotting, installation, sign-in and independent human review are
excluded and must be reported separately. Costs include failed/incomplete calls
and reservations where billing is unknown. A separate append-only provider journal
saves reservations before calls and returned/failed attempts immediately afterward.
Raw calls and receipts are sensitive.

## Blind review and metrics

Export the packet to a location separate from the experiment and give reviewers
only that packet. It contains original tasks, full source text, reference guidance
and findings, shuffled with opaque IDs. It omits policy, model, cost, timing and
execution status. Answer style can still partially reveal a model. Reviewers
should be independent of implementation and task tuning.

Set a non-identifying `reviewer_alias`. For **every original criterion**, set
`correct`, `supported` and `complete` to booleans. Missing/deferred supported work
is incomplete. Proper abstention on unsupported questions can be complete; it is
not automatically inferred from an empty answer. Usefulness is an anchored 0–4
rating. Null judgments are unreviewed, never automatic failures or successes.
Use two independent reviewers for the study, adjudicate disagreements, and retain
the original ratings and agreement analysis separately. The harness imports one
adjudicated packet; it does not implement reviewer agreement statistics.

```sh
python benchmarks/compare.py summarize --output PATH_TO_FROZEN_EXPERIMENT \
  --reviews PATH_TO_ADJUDICATED_PACKET
```

Changing packet content, removing criteria, unknown IDs, duplicates or invalid
grades is rejected. Only judgments, usefulness and notes are editable.

**Primary:** proportion of original tasks judged fully correct, supported and
complete, within both effective limits, with known billing and no execution/safety
failure. Unsupported criteria count according to their original rubrics. Success
rates are withheld until all planned runs in the comparison group are executed
and reviewed; synthetic execution is never eligible.

**Secondary:** quality pass rate, priority-weighted original-task completion,
partial usefulness, mean end-to-end time, total accounted cost, total cost per
successful task, limit misses, errors and provider calls. No successes means
undefined cost per success, not zero. Cost per success includes unsuccessful runs.
Group JSON supports quality/cost/time curves across conditions; it does not assert
quality equivalence from similar point estimates.

**Forecasts:** separately report original feasible preflight cost/time range
coverage and its denominator. They are heuristic ranges, not calibrated intervals.
No completion probabilities are invented.

Paired differences are Spawn minus each baseline within split and condition.
Average repetitions/tasks within declared related groups, then bootstrap paired
groups with the frozen seed. Intervals are descriptive and need enough independent
groups; one group has no interval. Report all conditions and failed runs. Stronger
claims require adequate power, independent data, multiple model configurations,
blind adjudication and uncertainty—not just a favorable mean.

Paired estimates weight related groups equally; pooled success rates weight planned
runs equally. Unequal group sizes can make these point differences differ.
