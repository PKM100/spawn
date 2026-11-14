"""Reproducible, bounded live pilot; no inference unless --live is supplied."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from spawn.contracts import Budget, Criterion, Model, Permissions, Plan, Request
from spawn.copilot_provider import list_models
from spawn.documents import load_documents
from spawn.planner import Planner, make_call
from spawn.providers import HTTPProvider, ProviderError
from spawn.runtime import Runtime

ROOT = Path(__file__).resolve().parent
SEED = 1729
POLICIES = ("spawn", "single_pass", "per_criterion")
PROFILE = Model(
    name="copilot-luna",
    provider="copilot",
    model_id="gpt-6-luna",
    base_url="https://github.com",
    input_usd_per_million=0.10,
    output_usd_per_million=0.50,
    output_tokens_per_second=100,
    input_tokens_per_second=2000,
    latency_seconds=3,
    context_tokens=64000,
    max_output_tokens=4000,
    per_call_usd_reserve=0.30,
    reasoning_effort="none",
)


class FixedPlanner(Planner):
    def __init__(self, policy):
        super().__init__([PROFILE])
        self.policy = policy

    def plan(self, request, documents):
        criteria = tuple(sorted(request.criteria, key=lambda c: -c.priority))
        groups = (
            (criteria,)
            if self.policy == "single_pass"
            else tuple((c,) for c in criteria)
        )
        strategy = "single_pass" if self.policy == "single_pass" else "incremental"
        calls = [
            make_call(request, group, documents, PROFILE, strategy) for group in groups
        ]
        feasible = (
            all(call is not None for call in calls)
            and sum(c.cost_high for c in calls) <= request.budget.max_usd
            and sum(c.seconds_high for c in calls) <= request.budget.max_seconds
        )
        return Plan(
            PROFILE if feasible else None,
            strategy,
            calls if feasible else [],
            [] if feasible else [c.id for c in criteria],
            1.0 if feasible else 0.0,
            {"percent": None, "calibrated": False, "status": "not_estimated"},
        )


class MeteredProvider:
    def __init__(self):
        self.calls = []
        self.total_attempts = 0
        self.total_accounted_usd = 0.0
        self.billing_unknown = False

    def complete(self, model, call, timeout):
        if (
            self.billing_unknown
            or self.total_attempts >= 24
            or self.total_accounted_usd + 0.30 > 8.0
        ):
            raise ProviderError("Pilot aggregate safety limit; no call started")
        self.total_attempts += 1
        started = time.monotonic()
        item = {"criteria": call.criteria, "prompt": json.loads(call.prompt)}
        self.calls.append(item)
        try:
            response = HTTPProvider().complete(model, call, timeout)
        except (ProviderError, TimeoutError):
            self.billing_unknown = True
            self.total_accounted_usd += 0.30
            item["failure"] = True
            raise
        finally:
            item["elapsed_seconds"] = time.monotonic() - started
        item["response"] = asdict(response)
        if response.reported_cost_usd is None:
            self.billing_unknown = True
        self.total_accounted_usd += (
            response.reported_cost_usd
            if response.reported_cost_usd is not None
            else 0.30
        )
        return response


def score(task, result):
    findings = {f["criterion_id"]: f for f in result["findings"]}
    checks = {}
    for cid, patterns in task["rubric"].items():
        finding = findings.get(cid)
        checks[cid] = (
            finding is None
            if patterns is None
            else finding is not None
            and all(re.search(pattern, finding["answer"], re.I) for pattern in patterns)
        )
    return {
        "criterion_checks": checks,
        "rubric_fraction": sum(checks.values()) / len(checks),
        "rubric_pass": all(checks.values()),
        "false_evidence_ready": result["status"] == "evidence_ready"
        and not all(checks.values()),
    }


def manifest():
    paths = [ROOT / "tasks.json", ROOT / "PROTOCOL.md", Path(__file__)]
    paths += sorted((ROOT / "corpus").glob("*.txt"))
    paths += sorted((ROOT.parent / "src" / "spawn").glob("*.py"))
    hashes = {
        str(path.relative_to(ROOT.parent)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }
    return {
        "files": hashes,
        "sha256": hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
    }


def request_for(task, output):
    return Request(
        task["goal"],
        tuple(ROOT / "corpus" / name for name in task["sources"]),
        tuple(Criterion(**item) for item in task["criteria"]),
        Budget(2, 120),
        Permissions((ROOT / "corpus",), output, (PROFILE.name,), True),
        model=PROFILE.name,
        task_class="authored-pilot-" + task["id"],
    )


def summarize(output):
    rows = [
        json.loads(line) for line in (output / "records.jsonl").read_text().splitlines()
    ]
    summary = {"runs": len(rows), "policies": {}, "paired_intervals": {}}
    for policy in POLICIES:
        selected = [r for r in rows if r["policy"] == policy]
        summary["policies"][policy] = {
            "runs": len(selected),
            "calls": sum(len(r["calls"]) for r in selected),
            "rubric_passes": sum(r["score"]["rubric_pass"] for r in selected),
            "criterion_checks_passed": sum(
                sum(r["score"]["criterion_checks"].values()) for r in selected
            ),
            "false_evidence_ready": sum(
                r["score"]["false_evidence_ready"] for r in selected
            ),
            "within_limits": sum(r["outcome"]["within_limits"] for r in selected),
            "total_usage_equivalent_usd": sum(
                r["outcome"]["accounted_cost_usd"] for r in selected
            ),
            "mean_end_to_end_seconds": statistics.mean(
                r["end_to_end_seconds"] for r in selected
            ),
            "time_scenario_coverage": statistics.mean(
                r["preflight"]["estimated_seconds_range"][0]
                <= r["end_to_end_seconds"]
                <= r["preflight"]["estimated_seconds_range"][1]
                for r in selected
            ),
            "cost_scenario_coverage": statistics.mean(
                r["preflight"]["estimated_cost_usd_range"][0]
                <= r["outcome"]["accounted_cost_usd"]
                <= r["preflight"]["estimated_cost_usd_range"][1]
                for r in selected
            ),
        }
    native = {r["task_id"]: r for r in rows if r["policy"] == "spawn"}
    for policy in POLICIES[1:]:
        paired = [r for r in rows if r["policy"] == policy and r["task_id"] in native]
        intervals = {}
        for metric in ("end_to_end_seconds", "accounted_cost_usd"):

            def value(row, metric=metric):
                return row[metric] if metric in row else row["outcome"][metric]

            differences = [value(native[r["task_id"]]) - value(r) for r in paired]
            rng = random.Random(SEED)
            samples = sorted(
                statistics.mean(rng.choices(differences, k=len(differences)))
                for _ in range(10000)
            )
            intervals[metric] = {
                "spawn_minus_baseline_mean": statistics.mean(differences),
                "paired_bootstrap_95_percentile": [samples[249], samples[9749]],
                "paired_tasks": len(paired),
            }
        summary["paired_intervals"][policy] = intervals
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live", action="store_true", help="Authorize billed model calls"
    )
    mode.add_argument("--summarize", action="store_true", help="No model calls")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "pilot-v1")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if args.summarize:
        print(json.dumps(summarize(output), indent=2))
        return
    tasks = json.loads((ROOT / "tasks.json").read_text())
    if not args.live:
        plans = [
            {
                "task": task["id"],
                "policy": policy,
                "preflight": (
                    Planner([PROFILE]) if policy == "spawn" else FixedPlanner(policy)
                )
                .plan(
                    request_for(task, output), load_documents(request_for(task, output))
                )
                .public(),
            }
            for task in tasks
            for policy in POLICIES
        ]
        print(json.dumps({"manifest": manifest(), "plans": plans}, indent=2))
        return
    output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.system() + " " + platform.machine(),
        "sdk_version": version("github-copilot-sdk"),
        "cli_version": subprocess.run(
            ["copilot", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip(),
        "profile": asdict(PROFILE),
        "seed": SEED,
        "manifest": manifest(),
        "catalog_model": next(m for m in list_models() if m["id"] == PROFILE.model_id),
        "pricing_source": "https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing",
        "setup_smoke": {
            "calls": 1,
            "seconds": 5.494494582992047,
            "usage_equivalent_usd": 0.0000225,
            "included_in_policy_comparisons": False,
        },
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    rng = random.Random(SEED)
    provider = MeteredProvider()
    for task in tasks:
        policies = list(POLICIES)
        rng.shuffle(policies)
        for policy in policies:
            started = time.monotonic()
            request = request_for(task, ROOT.parent / ".spawn" / "pilot-v1")
            planner = Planner([PROFILE]) if policy == "spawn" else FixedPlanner(policy)
            preflight = planner.plan(request, load_documents(request)).public()
            calls_start = len(provider.calls)
            result = Runtime(planner, provider).run(request)
            elapsed = time.monotonic() - started
            outcome = {
                key: value
                for key, value in result.items()
                if key not in {"sources", "artifacts"}
            }
            record = {
                "task_id": task["id"],
                "policy": policy,
                "preflight": preflight,
                "outcome": outcome,
                "end_to_end_seconds": elapsed,
                "calls": provider.calls[calls_start:],
                "score": score(task, result),
            }
            with (output / "records.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            print(
                f"{task['id']} / {policy}: {result['status']}, "
                f"rubric={record['score']['rubric_fraction']:.2f}, "
                f"{elapsed:.2f}s, ${result['accounted_cost_usd']:.7f}",
                flush=True,
            )
            if provider.billing_unknown:
                raise SystemExit("Stopped: complete billing telemetry unavailable")
    print(json.dumps(summarize(output), indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
