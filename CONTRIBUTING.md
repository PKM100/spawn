# Contributing

Spawn is a research MVP. Small fixes, provider-compatibility reports, better
evaluation tasks, and independently reviewed outcomes are especially useful.
Discuss architectural changes in an issue before implementing them.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,copilot]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check src tests benchmarks/*.py
.venv/bin/ruff format --check src tests benchmarks/*.py
.venv/bin/python benchmarks/run_pilot.py
.venv/bin/python -m pip wheel --no-deps . -w dist
```

Tests and the pilot's default dry run make no model calls. `--live` explicitly
authorizes billed Copilot inference and requires a signed-in CLI. Never run paid
experiments in pull-request CI. Preserve primary results; publish replications
under new directories and include failures, billing units, protocol manifests,
and limitations. Do not equate exact-quote checks with semantic correctness.

For matched-policy studies, see the [comparative benchmark](benchmarks/COMPARISON.md).
Its default run is synthetic; representative data and live spending need separate approval.

Keep changes focused and add tests for changed behavior. Do not commit credentials,
private documents, `.spawn/` receipts, personal paths, or account information.
Document provider assumptions and permission changes explicitly. Contributions
are licensed under the repository's MIT license.
