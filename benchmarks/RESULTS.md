# Live feasibility pilot v1

**Outcome:** working Copilot integration and reproducible research artifacts;
no demonstrated advantage over fixed single-pass batching, and uncalibrated
preflight scenarios. This is a feasibility study, not a finished paper.

Primary data: [records.jsonl](results/pilot-v1/records.jsonl),
[summary.json](results/pilot-v1/summary.json),
[environment/pricing/manifest](results/pilot-v1/metadata.json).
Protocol: [PROTOCOL.md](PROTOCOL.md). All evaluation runs are retained.

The primary experiment used v0.1.0, whose implementation is available at
[commit 4c91fff](https://github.com/PKM100/spawn/tree/4c91fff209d8501080ef5f440d3abf05cc1f3547).
Later wizard/skill changes are not covered by these results. The stored manifest
is intentionally unchanged; checking out that commit restores the measured code.

## Methods

On 2026-11-14, evaluate four authored tasks with two documents and three
criteria each, using three policies and one repetition. All use Copilot
`gpt-6-luna`, reasoning `none`, SDK 1.0.34 and CLI 1.0.112 on macOS/arm64,
Python 3.14.6. Randomize policy order within each task with seed 1729.

The Spawn arm uses the actual heuristic planner; the two fixed baselines use
all-criteria batching and one call per criterion. All share the actual runtime,
the same approved sources and model, evidence checks, $2 usage-equivalent budget,
and 120-second deadline. Each call starts a tool-free, isolated SDK session.
The model is pinned to avoid conflating scheduling with model quality.

The protocol, code, sources and rubrics were locally frozen before evaluation;
their SHA-256 manifest is saved before the first evaluation call. This is not
independently preregistered research. One empty-answer smoke call precedes the
freeze and is excluded from paired policy comparisons.

An accepted answer must include exact source quotes and satisfy its distinct-source
requirement. Every applicable frozen answer regex must then match for a criterion
check to pass. One unsupported certification criterion passes only if its finding
is omitted. Thus successful abstention can legitimately produce `partial`
execution status and less than 100% evidence coverage. This rubric does not
independently verify semantic support, answer correctness, or task completeness.

## Results

| Policy | Task rubric passes | Criterion checks | Calls | Mean end-to-end seconds | Total usage-equivalent USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| Spawn | 4/4 | 12/12 | 4 | 4.7708 | 0.0004618 |
| Fixed single pass | 4/4 | 12/12 | 4 | 4.6717 | 0.0004518 |
| Fixed per criterion | 4/4 | 12/12 | 12 | 12.1484 | 0.0007445 |

All 12 runs stayed within the configured limits. There were no provider failures,
Spawn-level retries, incomplete billing records, or false `evidence_ready` statuses
under the automated rubric. Every policy omitted the unsupported certification
finding. The authored fixture's invented Mesa study numbers are document contents,
not these measured results.

Call counts mean application-level `complete` / `send_and_wait` invocations.
SDK/provider-internal request retries were not separately instrumented. The
protocol's no-retry rule is enforced by Spawn and the experiment runner; it is
not a claim about unobserved provider behavior. Complete session billing totals,
rather than application call counts, determine the usage-equivalent cost.

Relative to the per-criterion loop, Spawn reduced usage-equivalent cost by 38.0%
and end-to-end time by 60.7%. Relative to fixed single pass, it was approximately
2.2% more costly and 2.1% slower. All Spawn plans selected single-pass batching.
The evidence supports an expected batching effect on these tiny tasks, **not**
a unique benefit from joint optimization, routing, or adaptive replanning.

### Paired descriptive uncertainty

Bootstrap task pairs 10,000 times, seed 1729. Values are **Spawn minus baseline**
mean differences. Negative differences favor Spawn.

| Baseline | Mean seconds difference | 95% percentile interval | Mean USD-equivalent difference | 95% percentile interval |
| --- | ---: | --- | ---: | --- |
| Fixed single pass | +0.0992 | [-0.0647, +0.2530] | +0.00000250 | [-0.00000050, +0.00000550] |
| Fixed per criterion | -7.3776 | [-7.5987, -7.1832] | -0.000070675 | [-0.00007595, -0.00006540] |

With four authored task pairs and one repetition, these intervals describe only
this sample's resampling variability. They are not reliable population-level
confidence statements and do not account for author, provider, or temporal bias.

### Forecast failure is a primary result

Spawn's cost/time scenario coverage was **0/4 for both quantities**. The same
was true for both fixed baselines. All observed values fell below the lower
scenario bounds. The profiles are manual assumptions, including generous output
budgets, not calibrated estimators. The $0.30 Copilot upper reservation is a safety
allowance and should not be confused with expected spend. These measurements do
not justify displaying a calibrated success percentage or prediction interval.
No completion probabilities were available, so no Brier score is reported.

### Accounting and timing

All 20 evaluation calls returned complete AI-credit telemetry. Copilot's
session-reported nano-AIU totals were divided by 1e9, then converted at
$0.01/AI credit. Total evaluation usage equivalent was **$0.0016581**.
The separate smoke call used **$0.0000225** and 5.4945 seconds.
The overall total was **$0.0016806** (0.16806 AI credits), far below the $10
authorized experiment ceiling. This is allowance-inclusive usage equivalent,
not incremental invoice charges; provider rounding and billing are authoritative.

Timing includes source reading, preflight, planning/replanning, fresh CLI/SDK
startup, inference, exact-quote checks, receipt writes and SQLite history.
It excludes installation, account sign-in, unbilled model discovery, and the
separately reported smoke call. Environment preparation is not claimed to be free.
The setup-smoke counters and timing are preserved in metadata; its empty-response
payload is not part of the task-policy response corpus.

## What a credible paper still needs

The present contribution is an instrumented open-source prototype plus a
transparent feasibility pilot. It is not a novel algorithm or validated scheduler.
The tasks are short, authored by the implementer, correlated, and not independently
held out. There is one model, one local architecture, one repetition, no human
scoring, no constrained operating-point sweep, and no separate model-profile
training set. Provider cache state, load, and drift are uncontrolled.

A larger study should freeze independent task families and model profiles;
measure overhead-inclusive cost/time distributions; calibrate forecasts on a
separate split; use blind semantic scoring; vary deadlines and budgets; include
multiple models, seeds and time-forward tests; and compare strong adaptive,
budget-aware and routing baselines. Ablate batching, scope reduction, routing,
and replanning separately. Contrast proposed mechanisms with BATS, Switchcraft,
Agentic Plan Caching and other close prior work before making a novelty claim.

## Reproduce the analysis

```sh
.venv/bin/python benchmarks/run_pilot.py --summarize
.venv/bin/python benchmarks/run_pilot.py  # current dry-run preflights, no inference
```

To replicate live, use a **new output directory** with `--live`; do not overwrite
or cherry-pick the primary study. A changed implementation must have its own
manifest. Preserve all failures and identify the exact billing units.
