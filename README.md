# Spawn

A Python CLI and library for local-document research. Set a goal, sources, model,
budget and deadline; review the plan, then approve execution.

Document research is the first use case. The approach can extend to other domains
with task-specific tools and completion checks; those integrations are not built yet.

[Website](https://pkm100.github.io/spawn/) · [Initial pilot](benchmarks/RESULTS.md) ·
[Contributing](CONTRIBUTING.md)

## Install

Requires Python 3.11+ and [pipx](https://pipx.pypa.io/stable/installation/).

```sh
git clone https://github.com/PKM100/spawn.git
cd spawn
pipx install '.[copilot]'
pipx ensurepath
spawn
```

Open a new terminal if PATH changed. Copilot requires a separate
[CLI sign-in](https://docs.github.com/en/copilot/how-tos/copilot-cli).
No PyPI release yet.

<a id="real-document-research"></a>

## Usage

`spawn` opens a wizard—no configuration files needed. It collects the task and
permissions, shows cost/time scenarios and scheduled work, then asks for approval.
Runs save a cited report, trace and receipt under the chosen output directory.
Work that does not fit is deferred.

```sh
spawn demo           # Offline, synthetic responses; no paid calls
spawn models --json  # Copilot discovery; no inference
spawn --help
```

### Models

Use your Copilot sign-in, OpenAI-compatible endpoints or Anthropic.
Pin a model or let the scheduler choose among approved profiles.
Direct APIs require token prices and an API-key environment variable, not a key
pasted into Spawn. See the [Copilot profile](examples/copilot-models.toml) and
[model template](src/spawn/templates/models.toml).

### Agents

The wizard offers optional skills for Claude Code, Codex and Copilot. Or install
them explicitly:

```sh
spawn skills install --agent all --scope user
# Use --scope project for the current directory only.
```

Skills are bundled with Spawn, but installation requires consent. They enable
task-matched document research—not coding loops or automatic spending.
Invocation depends on the host; reload its skills if needed.

Agents can pipe a JSON contract to `spawn plan --stdin --json`, then, after
approval, to `spawn run --stdin --yes --json`. See the
[skill instructions and contract example](src/spawn/templates/SKILL.md).

### Optional configuration files

```sh
spawn init my-research
# Edit the generated request and model profiles.
spawn launch my-research/request.json --models my-research/models.toml
```

File-based paths resolve relative to the request; stdin paths resolve from the
working directory. `--yes` requires explicit `send_documents: true`.

## Limits

- Reads approved local text files. No shell, web browsing or arbitrary tools.
- Cost/time scenarios and success estimates are **not calibrated**. Exact quotes
  do not establish answer correctness.
- Provider spending caps and deadlines are best-effort. Copilot reserves at least
  **$0.30 per call**; reported dollars are usage equivalents, not invoice charges.
- Reports and receipts can contain source text and paths. Treat them as sensitive.

## Evaluation

**Representative benchmarks are pending.** The initial four-task feasibility
pilot does not establish a performance advantage.

Evaluation needs independent tasks, matched baselines, repeated runs and reviewed
completion outcomes. Cost, time and usefulness must include execution overhead.

[Protocol](benchmarks/PROTOCOL.md) ·
[Initial pilot and limitations](benchmarks/RESULTS.md) ·
[Raw pilot data](benchmarks/results/pilot-v1)

The current release has 76 automated tests. Development and validation commands
are in [CONTRIBUTING.md](CONTRIBUTING.md).

[MIT license](LICENSE) · [Citation](CITATION.cff)
