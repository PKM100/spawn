# QASPER development results

**Finding:** larger question bundles show the expected runtime benefit of batching,
but do not establish a Spawn-specific advantage over greedy budget-aware batching.
These are exploratory development measurements, not reviewed quality results or
a completed held-out evaluation.

[All recorded measurements](results/qasper-development-v1/measurements.json) ·
[Task set and attribution](QASPER.md) · [Comparison protocol](COMPARISON.md)

## Studies

Both studies ran on 2026-11-14 at
[commit 228684e](https://github.com/PKM100/spawn/tree/228684ecc2b63a4c4e0d7a73540a1c44aea2a868).
All four policies share the runtime, lexical excerpt selector, exact-quote checks
and Copilot `gpt-6-luna` model, reasoning `none`. Scheduling is pinned to that model;
these experiments do not test model routing.

| Development study | Task structure | Recorded/planned runs | Calls | Total USD equivalent | Sum of case seconds |
| --- | --- | ---: | ---: | ---: | ---: |
| Short pilot | First two papers; 11 original questions | 16/16 | 30 | 0.005368725 | 170.705 |
| Larger bundles | All 20 papers; 80 questions in five bundles | 99/120 | 500 | 0.238233070 | 2656.757 |

The short pilot used one repetition and generous/constrained limits.
The larger study used two repetitions and bundles of 15–17 questions from 3–5
papers. Bundle membership follows the original development selection order,
closing each bundle at at least 15 questions. Paper-title prefixes disambiguate
the original questions; no reference-derived priorities or synthesis questions
were added. Task/condition blocks and policy order were randomized with seed 1729.

Larger-study limits were $6/600 seconds (generous), $1.20/90 seconds (constrained),
and $6/600 seconds shortened to a **90-second total deadline after 10 seconds**.
Output allowances fit every original criterion under the planner's assumptions;
the single-pass baseline was not blocked by an undersized output allowance.

The larger study stopped at its authorized **500-call cap**, not its $10 spending
ceiling. The final recorded run was cut short; 21 planned runs were never started.
All 500 calls returned known billing and all 99 recorded runs stayed within their
effective limits. There were no provider failures. This does not make the final
run complete or the study successful.

## Observed runtime

Mean end-to-end seconds from **24 fully observed matched blocks**: eight blocks
per condition, four policies per block. These 96 runs are an exploratory subset;
the three additional records from the final partial block remain in the public
measurements. A call-count stopping rule can affect which blocks are observed.

| Condition | Spawn | Fixed single pass | Fixed per criterion | Greedy budget-aware |
| --- | ---: | ---: | ---: | ---: |
| Generous | 11.88 | 12.44 | 68.26 | 12.65 |
| Constrained | 27.56 | No call | 67.29 | 26.35 |
| Deadline shortened | 10.57 | 12.58 | 65.45 | 10.56 |

Under generous limits, mean usage-equivalent cost was $0.001292 for Spawn,
$0.001332 for fixed single pass, $0.005000 for per criterion, and $0.001345 for
greedy batching. These costs and times are **not quality-equivalent comparisons**.

Spawn, fixed single pass and greedy batching issued identical full-batch prompts
in every fully observed generous and deadline-change block. Output differences
between these arms cannot establish an advantage from scheduling. Under
constrained limits, Spawn and greedy batching had similar observed runtimes.
The fixed single-pass policy refused all constrained tasks based on conservative,
uncalibrated preflight assumptions. Its zero-call latency is not an efficiency
win, nor does its refusal establish superiority over a calibrated baseline:
observed generous full-batch runs were well below the 90-second deadline.

## What these measurements do not establish

Independent semantic review is pending. Exact-quote acceptance does not establish
correctness, semantic support or completeness; legitimate abstention can reduce
quote coverage. Primary success rates, cost per successful task, quality-equivalent
savings and statistical superiority are therefore withheld.

There are **five bundle groups**, not 120 independent tasks. The tasks still ask
independent questions, rather than making later actions depend on earlier results.
Many larger tasks consequently collapse to one batch. A mid-call deadline change
does not interrupt that call; it is observed at subsequent call boundaries.
Profiles and operating points remain uncalibrated. There is one model, public
data with possible training contamination, and no pinned provider model revision.

Costs are allowance-inclusive Copilot usage equivalents, **not invoice charges**.
Timing includes ingestion, planning, isolated CLI/SDK sessions, calls, validation
and artifacts; it excludes study preparation, installation, sign-in and review.
SDK 1.0.34 and CLI 1.0.112 were used. The installed package metadata reports 0.1.0;
the measured checkout and implementation hashes, not that stale label, identify
the code.

## Research framing

> We present an instrumented, permission-bound research executor and characterize
> its behavior under matched resource constraints. Development experiments show
> lower observed runtime for batching than a per-criterion loop, but no distinct
> advantage over a strong greedy batching baseline. Larger independent-question
> bundles do not by themselves create adaptive long-horizon workloads. These
> findings motivate calibration, independently reviewed outcomes, and evaluations
> in which earlier results change later decisions.

Frame this as **systems characterization and a boundary finding**, not a validated
novel scheduler, evidence of better answers, or a “cheaper and faster agent” claim.
The next meaningful test needs genuinely dependent workflows and independent
quality review, followed by a frozen held-out study.

The public JSON contains every recorded case's whitelisted measurements, frozen
input/code hashes and exploratory matched aggregates. Paper text, questions,
reference answers, model responses and blind-review packets remain local under
ignored `.spawn/`; they are not republished as MIT fixtures. Neither the original
pilot nor unfavorable or incomplete measurements have been removed.
