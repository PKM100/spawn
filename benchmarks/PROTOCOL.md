# Spawn pilot v1: frozen protocol

This is a small, live feasibility pilot, not a publication-quality efficacy
benchmark. Freeze this file, the runner, rubrics, sources, and Spawn implementation
before the first evaluation call. The runner records their SHA-256 manifest.
Model discovery and one empty-answer smoke call precede evaluation. Do not tune
on evaluation responses or silently replace failed runs.

## Design

Four authored document pairs, three criteria each, three policies, one repetition:
12 paired task-policy runs and 20 intended model calls. All policies use
Copilot `gpt-6-luna`, reasoning `none`, identical evidence instructions, and fresh
tool-free SDK sessions. Available tools, custom instructions, configuration
discovery, skills, hooks, compaction, and session persistence are disabled.
Rubrics are not sent to the model.

Policies:

- **Spawn:** the unmodified joint heuristic planner; model pinned for comparability.
- **Single pass:** fixed all-criteria batching; no scope reduction.
- **Per criterion:** fixed one-criterion calls; no scope reduction.

All use the real runtime, exact-quote checker, approved corpus, $2 usage-equivalent
run budget and 120-second deadline. Baselines refuse infeasible initial plans
rather than ignoring constraints. Run policy order is randomized within task
with seed 1729. This does not control provider load, cache state, or model drift.
These unconstrained operating points do not test deadline adaptation or routing.

## Scoring and outcomes

The frozen regex rubric checks predefined answer facts, conditional on Spawn's
exact-quote/source validation. Every pattern must match an accepted answer.
One criterion is deliberately unsupported: omitting its finding passes its
rubric. An explicit unsupported claim fails. The model may legitimately achieve
less than 100% evidence coverage on that task. A rubric pass is **not** a human
entailment judgment or proof of factual correctness. Fixture numbers are invented
document contents, never benchmark measurements.

Report task rubric passes, criterion checks, false `evidence_ready` (ready status
but rubric failure), limit compliance, calls, complete billing telemetry, total
usage-equivalent dollars, and mean end-to-end time. End-to-end time includes
preflight, document reading, planning/replanning, CLI/SDK startup, model calls,
evidence checks, receipts and history writes; excludes environment installation,
initial sign-in, discovery, and the separately reported smoke call. Failed calls
retain reservations. No retries or reruns are allowed in the primary pilot.

Preserve original preflight ranges and report their empirical coverage.
These are heuristic scenarios, not calibrated intervals. No success probabilities
are available and no Brier score or calibration claim can be made.
Report paired task-bootstrap percentile intervals (10,000 resamples, seed 1729)
for Spawn-minus-baseline mean time and cost. Four tasks are far too few to make
these intervals reliable population-level uncertainty statements.

## Accounting and safety

Use complete session-reported Copilot AI credits (nano-AIU / 1e9) at $0.01/credit,
or explicitly identified legacy premium-request equivalents at $0.04/request.
These are allowance-inclusive usage equivalents, not incremental invoice spend
or directly billed API token costs. Cache/reasoning counters remain in raw records.
Record model-catalog pricing, CLI/SDK versions, hardware architecture, and date.

Reserve at least $0.30/call because Copilot's 30-credit minimum session cap is
postpaid/soft. Output length is requested, not hard-enforced by the SDK. There
is no strict dollar guarantee. Stop the study on incomplete billing or a provider
failure. Limit evaluation to 24 attempted calls and $8 accounted-plus-next-call
reservation, leaving headroom within the approved $10 overall experiment budget.
The one smoke call is reported separately. Do not upload private documents,
credentials, account IDs, absolute local paths, or private run directories.

## Reproduction

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,copilot]'
# Install Copilot CLI and sign in separately.
.venv/bin/python benchmarks/run_pilot.py             # no inference
.venv/bin/python benchmarks/run_pilot.py --live --output .spawn/new-pilot
.venv/bin/python benchmarks/run_pilot.py --summarize --output .spawn/new-pilot
```

Live mode refuses an existing output directory. New runs must be reported as
replications, not substituted for the primary results. A credible paper still
needs larger independently sourced held-out tasks, multiple seeds/models,
independent semantic scoring, constrained operating points, stronger adaptive
baselines, measured model profiles, and a defensible novelty argument.
