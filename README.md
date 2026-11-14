# Spawn

**Know the tradeoff. Then spawn.**

[Website](https://pkm100.github.io/spawn/) ·
[Pilot results](benchmarks/RESULTS.md) ·
[Protocol](benchmarks/PROTOCOL.md) · [Contributing](CONTRIBUTING.md)

A Python library and terminal interface for permission-bound document research:
preview cost/time scenarios, choose a model and strategy, approve the execution
envelope, and receive a cited report plus an execution receipt.

**Research MVP, not a validated optimizer.** The current scheduler uses explicit
heuristics and user-supplied model profiles. A small live pilot is published below;
it does not establish general savings, calibrated forecasts, or research novelty.

## Try the terminal UI

Requires Python 3.11+.

```sh
git clone https://github.com/PKM100/spawn.git
cd spawn
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/spawn
.venv/bin/spawn demo
```

The demo is entirely offline. It uses synthetic responses, simulated token
accounting, and a synthetic research note. It writes receipts under `.spawn/demo`.
It does not call a provider or demonstrate real model quality or latency.

The terminal UI includes:

- Cost/time scenarios, planned coverage, and a separate success-probability card.
- Model and strategy comparison, prioritized deliverables, and deferred work.
- A read/write/data-sharing permission envelope and explicit upfront approval.
- Live evidence progress, model calls, accounting, and a final report receipt.
- An interactive preflight menu for model, spending limit, and deadline changes.

Rich output adapts to terminal width. `NO_COLOR=1` disables color. Source text and
user-supplied strings are rendered as text, not executable Rich markup.

## Real document research

```sh
.venv/bin/spawn init my-research
```

Edit `my-research/models.toml` and `my-research/request.json`. Replace placeholder
model IDs, example pricing, throughput assumptions, and source documents. **The
example numbers are not current vendor prices or measured benchmarks.**

```sh
.venv/bin/spawn plan my-research/request.json --models my-research/models.toml
.venv/bin/spawn launch my-research/request.json --models my-research/models.toml
```

`launch` lets you revise the preflight interactively before giving approval.
It can approve data sharing for that run without modifying the request file.
Credentials are read from the model profile's environment-variable name, never
stored in example configurations or traces. Set `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY` in your environment as appropriate.

For automation, explicitly set `permissions.send_documents` to `true`, then:

```sh
.venv/bin/spawn plan my-research/request.json \
  --models my-research/models.toml --model fast --seconds 30 --json

.venv/bin/spawn run my-research/request.json \
  --models my-research/models.toml --yes --json
```

Without `--yes`, `run` requires an interactive approval. `--yes` does not expand
the permissions in the request. Paths in a request are relative to that request
file, not the current working directory. `init` never overwrites an existing
directory.

### A request is an execution contract

```json
{
  "goal": "Compare the methods and limitations in the supplied notes.",
  "sources": ["notes/a.md", "notes/b.md"],
  "criteria": [
    {
      "id": "methods",
      "description": "Compare the two methods using both sources.",
      "priority": 3,
      "min_sources": 2
    },
    {
      "id": "limitations",
      "description": "Identify limitations and remaining questions.",
      "priority": 1
    }
  ],
  "budget": {"max_usd": 0.25, "max_seconds": 90},
  "permissions": {
    "read_roots": ["notes"],
    "output_root": ".spawn",
    "allowed_models": ["fast", "alternative"],
    "send_documents": false
  },
  "allow_scope_reduction": true,
  "on_failure": "defer",
  "task_class": "methods-comparison"
}
```

Spawn loads only explicitly listed UTF-8 `.txt`, `.md`, `.rst`, `.csv`, and `.json`
files inside approved read roots, resolving symlinks before checking access.
Documents are limited to 2 MB each and 10 MB in total. Source files are never
modified. There is no shell execution, PDF parsing, live web search, or arbitrary
tool execution in this MVP. Excerpts are selected with a simple lexical heuristic.

`single_pass` answers selected criteria in one model call; `incremental` answers
one criterion at a time for smaller contexts and partial deliverables. The
planner maximizes scheduled priority coverage, then considers a user-configured
model preference, estimated cost, and estimated time. It does **not** infer that
a more expensive model is better, or use unsupported quality estimates.
Set `"allow_scope_reduction": false` to reject plans that omit any criterion.
Larger integer `priority` values are more important.

### Bring a model

Add a named model profile to the TOML array. Arbitrary model IDs are accepted
through OpenAI-compatible Chat Completions endpoints or Anthropic Messages
endpoints, or the optional Copilot SDK, subject to API compatibility and account
access. This is not a claim that every model
will obey the evidence schema or have predictable performance.

For OpenAI-compatible servers, configure `output_limit_field` as
`max_completion_tokens` or `max_tokens`, and `system_role` as `system` or
`developer`. `base_url` includes the API prefix (usually `/v1`), not the final
`/chat/completions` or `/messages` path. HTTPS is required except for explicit
loopback hosts. Credential-bearing redirects are not followed. No automatic
retry is performed; failures may still incur provider charges.

`--model NAME` pins an approved profile. Without a pin, the runtime may reselect
among approved profiles as remaining time, budget, and scope change.

### Use your Copilot sign-in

Install and sign in to the [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli)
separately, then add the optional SDK dependency from this checkout:

```sh
.venv/bin/python -m pip install -e '.[copilot]'
.venv/bin/python -c \
  'from spawn.copilot_provider import list_models; print([m["id"] for m in list_models()])'
```

Discovery does not run inference. Copy [the Copilot profile](examples/copilot-models.toml)
into your model configuration, approve its name in `permissions.allowed_models`,
and run the usual `plan` / `launch` commands. Pin a discovered model ID if desired.
Raise both the request budget and any control-file dollar limit to at least
$0.30 for one Copilot call; the default example's $0.10 limit correctly blocks it.
SDK 1.0.34 is pinned because its session/usage interfaces are evolving; the primary
pilot used CLI 1.0.112. Future CLI versions may require compatibility changes.

Copilot runs in a fresh temporary working/storage directory, in SDK `empty` mode,
with no tools, configuration discovery, custom instructions, skills, hooks,
compaction, host Git operations, or persistent session store. Permission requests
are denied. Authentication reuses the installed CLI's signed-in account; Spawn
does not extract or store its token. `base_url` is descriptive for this backend,
not a custom Copilot gateway. OpenAI/Anthropic credentials are not required.

**Accounting differs from direct APIs.** Complete session-reported AI credits
are converted at $0.01/credit; explicitly identified legacy premium requests
use $0.04/request. These are usage equivalents, **not incremental invoice spend**.
Incomplete billing retains the full reservation even when token counts exist.
Copilot profiles require `per_call_usd_reserve >= 0.30` because the SDK's minimum
session limit is 30 credits. This is a conservative reservation, not the expected
charge. The cap is soft/postpaid and the requested output length is not
hard-enforced: neither this setting nor `--usd` guarantees a provider-side cap.

### Tighten a running deadline

An optional `control_file` must be inside an approved read root:

```json
{"max_seconds": 30, "max_usd": 0.10}
```

Limits are absolute totals from run start, not additional allowances. The runtime
reads the file at model-call boundaries, replans unfinished work, and retains
completed findings. Larger limits are ignored; new models, permissions, and
other decisions cannot be introduced through this file. To increase authority,
start a new approved run.

Updates do not interrupt an in-flight HTTP call immediately. Network timeouts are
best-effort; DNS, server behavior, and billing can exceed assumptions. A reduced
limit can already be exceeded when it is received. In that case Spawn stops
starting work and reports the violation. Ctrl-C during a model call preserves
prior findings and retains a reservation for the interrupted request.

### Understand the estimates

| Quantity | Current meaning |
| --- | --- |
| Cost/time ranges | Profile-based scenarios, **not** calibrated prediction intervals. |
| Planned coverage | Priority-weighted fraction of criteria scheduled, not success probability. |
| Evidence coverage | Fraction with accepted answers and the required distinct-source exact quotes. |
| Success probability | Unavailable at cold start. After 20 manually labeled comparable runs, a historical rate with a Wilson interval; still explicitly **not calibrated**. |
| Accounted cost | Direct APIs: reported tokens at configured prices. Copilot: reported credit/request equivalents. Reservations remain when billing is unavailable or a call fails. |

Exact quote existence does not establish semantic support, correctness,
comprehensiveness, or source trustworthiness. `evidence_ready` is not a claim
that a human has verified the full task. The system never labels its own
successes for forecasting.

Historical cohorts use task class, provider/model, strategy, criterion/source
counts, and source-size and budget buckets. They are coarse and may pool
different tasks. Repeated correlated tasks, model updates, and selective human
labeling can bias rates and invalidate the interval's independence assumptions.
Twenty observations are a display threshold, not a scientific calibration claim.
Changed strategies, model combinations, scope-reduced runs and live budget changes
are excluded from matching the original cohort.

Token reservations assume at most one token per UTF-8 byte plus 2048 protocol
tokens, and output bounded by the configured request limit. This assumption must
be validated for a new backend. If reported usage exceeds it, the runtime reports
the violation and starts no further calls. Actual provider bills are authoritative.
Cache discounts are not assumed. No spending guarantee is made for an arbitrary
provider or incorrect pricing profile.

## Artifacts and evaluation

Each run gets an isolated directory under the approved output root:

```text
<run-id>/
  preflight.json
  trace.jsonl
  report.md
  result.json
history.sqlite3
```

Reports contain source quotes and source names. Receipts contain source paths and
hashes. Traces exclude prompts, model output text, and credentials, but include
the goal-independent route, criteria IDs, endpoint URLs, and accounting metadata.
Treat all artifacts as potentially sensitive. Spawn has no independent telemetry
or artifact upload; provider requests send the explicitly approved excerpts.
Third-party providers and the installed CLI have their own data policies.

After independently reviewing the **full original contract**, record an outcome:

```sh
.venv/bin/spawn label RUN_ID --history my-research/.spawn/history.sqlite3 --success
# Or use --failure. Partial, over-limit, unpriced and synthetic runs cannot be
# labeled successful.
```

The evaluator accepts JSONL rows with the following shape:

```json
{"run_id":"held-out-1","split":"test","prediction":{"cost_usd":[0.01,0.03],"seconds":[5,15],"success_probability":null},"outcome":{"cost_usd":0.02,"seconds":12,"success":true}}
```

```sh
.venv/bin/spawn evaluate held-out.jsonl --json
```

It reports Brier score where probabilities are available, reliability bins,
midpoint MAE, and observed interval coverage. Marking rows `test` cannot establish
independence: the experimenter must freeze the policy, preserve original preflight
predictions, use trusted billing/timing measurements and reviewed outcomes, and
prevent training/test leakage.

### Research question and falsifiable protocol

**Question:** Can joint preflight forecasting and adaptive execution improve
deadline-constrained document-research success at a fixed spending limit, compared
with simpler policies, while accurately forecasting outcomes?

The current implementation is an instrumented baseline for investigating that
question, not evidence that the hypothesis is true or that the mechanism is new.

An initial experiment should use a frozen corpus and held-out task families with
explicit answer/citation rubrics. Compare the same model pool and tools under
fixed-model single-pass execution, a fixed per-criterion loop, simple budget-aware
rules, per-call model routing, and Spawn's joint scheduler. Relevant prior work
includes [BATS](https://arxiv.org/abs/2511.17006),
[Switchcraft](https://arxiv.org/abs/2605.07112), and
[Agentic Plan Caching](https://proceedings.neurips.cc/paper_files/paper/2025/hash/9549f7d06700f0966d5f938f1d11022a-Abstract-Conference.html).

Use paired tasks and matched tuning effort. Report success, false completion,
deadline-miss rate, human-rated partial usefulness, cost per successful task, and
cost/time forecast coverage across budgets. Include preparation, controller,
verification, retry and profiling overhead, and separate one-time training costs.
Use held-out models/tool configurations and time-forward tests for transfer.
Report paired uncertainty estimates and ablate call batching, scope reduction,
and model switching separately. Do not select operating points on the test set.
No claimed percentage savings or publication result follows from the offline demo.

### Live feasibility pilot — 2026-11-14

**60 automated tests pass**, separately from the live experiment. The live pilot
used four authored document pairs, three frozen criteria per task, three policies,
one repetition, and Copilot `gpt-6-luna` with reasoning disabled. All arms used
the same runtime, permissions, model, evidence checks, $2 usage-equivalent budget,
and 120-second deadline. There were 12 task-policy runs and 20 live evaluation
calls. This is not a representative or independently held-out benchmark.

| Policy | Task rubric passes | Model calls | Mean end-to-end time | Total Copilot usage equivalent |
| --- | ---: | ---: | ---: | ---: |
| Spawn | 4/4 | 4 | 4.77 s | $0.0004618 |
| Fixed single pass | 4/4 | 4 | 4.67 s | $0.0004518 |
| Fixed per criterion | 4/4 | 12 | 12.15 s | $0.0007445 |

Spawn used 38.0% less usage-equivalent cost and 60.7% less time than the
per-criterion loop **in this pilot**, but did not outperform fixed single-pass
batching. The result supports batching on these small tasks, not superiority of
the joint scheduler. The frozen regex rubric passed 36/36 criterion checks
across all arms, including an abstention criterion; this is **not** independent
semantic verification or calibrated task-success probability.

**Important negative result:** Spawn's cost and time scenario ranges each covered
0/4 observed outcomes; actual usage/time were below their lower bounds.
Forecasts are not calibrated and must not be marketed as reliable intervals.
All runs stayed within their configured limits, but these generous budgets do
not validate adaptive deadline handling or model routing.

The entire experiment, including one separately logged setup smoke call, used
about **$0.0016806 in Copilot usage equivalent**, not incremental billed dollars.
End-to-end policy timing includes preflight, SDK/CLI startup, calls, checks and
receipts; excludes installation, sign-in, discovery and the setup smoke.

See the [locally frozen protocol](benchmarks/PROTOCOL.md),
[raw responses, usage, hashes and versions](benchmarks/results/pilot-v1),
and [results, paired intervals and paper limitations](benchmarks/RESULTS.md).
Recompute the summary without a model call:

```sh
.venv/bin/python benchmarks/run_pilot.py --summarize
```

## Python API

```python
from pathlib import Path
from spawn import Planner, Runtime
from spawn.config import load_models, load_request
from spawn.documents import load_documents

request = load_request(Path("my-research/request.json"))
planner = Planner(load_models(Path("my-research/models.toml")))
forecast = planner.plan(request, load_documents(request))
print(forecast.public())

# Requires send_documents=true in this explicit permission envelope.
result = Runtime(planner, on_event=lambda event: print(event["type"])).run(request)
```

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check src tests benchmarks/run_pilot.py
.venv/bin/ruff format --check src tests benchmarks/run_pilot.py
```

Tests use local fixtures and fake responses, not paid APIs. Provider request and
response compatibility tests do not establish live service availability. Installing
`.[dev,copilot]` runs the optional SDK isolation test as well. See
[CONTRIBUTING.md](CONTRIBUTING.md) for wheel builds and pilot reproduction.

## License and citation

[MIT](LICENSE), including the authored benchmark fixtures. Cite the software with
[CITATION.cff](CITATION.cff) and identify the commit used. There is no associated
peer-reviewed paper yet. The project is distributed from this repository; no
PyPI release or package-name availability is claimed.
