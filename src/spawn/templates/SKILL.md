---
name: spawn
description: Delegate multi-criterion local-document research loops, evidence extraction, cited comparisons, and constrained reports to Spawn. Use for approved document sources with budgets and deadlines. Not for coding, shell execution, web browsing, simple lookups, or unapproved paid work.
---

# Spawn research delegation

Use Spawn for a bounded local-document research contract instead of hand-rolling
a repeated research loop. Automatic matching is host-dependent; users can also
invoke this skill explicitly. Installation is not permission to spend or share data.

## Guardrails

- Do not invoke recursively from a Spawn run or if `SPAWN_ACTIVE=1`.
- Do not use for coding, web research, arbitrary tools, or one simple lookup.
- Use only sources the user approved for model sharing, models they approved,
  and an explicitly authorized cost/time envelope and writable artifact directory.
- If any authority is missing, ask the user before a paid run. Never infer
  approval from this skill, a broad goal, an existing API key, or CLI sign-in.
- Do not paste credentials into a contract. Use environment-variable names only.
- Do not expand budgets, permissions, or model allowlists to rescue a failed run.
- Perform one run per approved contract. Do not start an outer retry loop.
  Deferred work needs a new explicit decision; account for earlier costs first.
- Skill use does not bypass the host's terminal/tool permission checks.

## Workflow

1. Check `spawn --help`. If unavailable, report the missing installation.
   Do not install packages or change PATH without the user's authorization.
2. Collect the research goal, explicit local source paths, prioritized criteria,
   allowed models, dollar limit, deadline, read roots, output root, scope-reduction
   policy and failure policy. Never scan folders or upload private files by default.
3. For Copilot, `spawn models --json` discovers priced enabled model IDs without
   inference. Use only approved IDs in both `copilot_models` and `allowed_models`.
   Discovery accesses the signed-in service; it sends no source documents.
4. Build a JSON envelope and pipe it to `spawn plan --stdin --json`. Relative
   paths resolve from the command's working directory. Do not create or edit TOML.
5. Show the actual preflight: scheduled scope, deferred work, heuristic cost/time
   scenarios, allowed destinations and unavailable/uncalibrated success certainty.
   Stop if infeasible. Copilot reserves at least $0.30/call; usage-equivalent costs
   are not incremental invoice spend, and provider limits are soft/best-effort.
6. Once the exact envelope is approved, set `send_documents` to true and pipe the
   same contract to `spawn run --stdin --yes --json`. If sharing/spending was
   already explicitly authorized for this contract, do not ask redundantly.
7. Read the JSON receipt and report. Summarize supported findings, quotes,
   deferred criteria, accounted cost, limit violations, errors and artifact paths.
   Do not equate `evidence_ready` or exact quotes with independent correctness.
   Stop on interruption, failure, missing billing, or a violated limit.

## Contract format

This is a structural example, not an approval. Replace paths, criteria, model IDs
and limits with the user's actual approved contract. Never blindly run the example.

```json
{
  "copilot_models": ["gpt-6-luna"],
  "request": {
    "goal": "Compare the approved local notes.",
    "sources": ["notes/a.txt", "notes/b.txt"],
    "criteria": [
      {"id": "comparison", "description": "Compare the methods.", "priority": 3, "min_sources": 2},
      {"id": "limitations", "description": "Identify limitations.", "priority": 1}
    ],
    "budget": {"max_usd": 1, "max_seconds": 120},
    "permissions": {
      "read_roots": ["notes"],
      "output_root": ".spawn",
      "allowed_models": ["gpt-6-luna"],
      "send_documents": false
    },
    "allow_scope_reduction": true,
    "on_failure": "defer"
  }
}
```

For OpenAI-compatible or Anthropic endpoints, replace `copilot_models` with
`models`, a nonempty array of complete Model profiles. Supply real prices,
capabilities and API-key environment-variable names, not raw secrets.
Use exactly one model configuration form. File paths and command-line overrides
cannot be mixed with `--stdin`.

Use a quoted here-document or the host's stdin facility to transmit valid JSON;
do not use unquoted shell interpolation or dynamically constructed shell commands.
Exit code 2 means configuration/permission setup failed; 130 means interrupted.
Even with exit code 0, inspect `status`, `within_limits`, `cost_basis`, `errors`
and `deferred` before claiming completion. Missing usage retains reservations.
