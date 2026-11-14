"""Frozen document-research comparisons. Default execution is synthetic, not live."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import re
import statistics
import sys
import threading
import time
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from spawn.config import load_models
from spawn.contracts import (
    Budget,
    Criterion,
    Model,
    Permissions,
    Plan,
    Request,
    positive,
)
from spawn.documents import load_documents
from spawn.planner import Planner, make_call
from spawn.providers import HTTPProvider, ProviderError, Response
from spawn.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]
POLICIES = ("spawn", "single_pass", "per_criterion", "budget_aware")
JUDGMENTS = ("correct", "supported", "complete")


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def write_json(path, value):
    path.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise ValueError("IDs must contain 1-64 letters, numbers, _ or -")
    return value


def implementation():
    paths = sorted((ROOT / "src/spawn").rglob("*.py"))
    paths += [
        Path(__file__),
        ROOT / "benchmarks/COMPARISON.md",
        ROOT / "pyproject.toml",
    ]
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def environment():
    packages = {}
    for package in ("spawn-agent", "github-copilot-sdk", "rich"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
    }


class Baseline(Planner):
    """Fixed batching, a fixed queue, or greedy budget-aware prefix batching."""

    def __init__(self, models, policy):
        super().__init__(models)
        if policy not in POLICIES[1:]:
            raise ValueError("Unknown baseline")
        self.policy = policy

    def plan(self, request, documents):
        criteria = tuple(
            sorted(request.criteria, key=lambda criterion: -criterion.priority)
        )
        candidates = []

        def candidate(model, strategy, calls):
            selected = {cid for call in calls for cid in call.criteria}
            candidates.append(
                Plan(
                    model,
                    strategy,
                    calls,
                    [
                        criterion.id
                        for criterion in criteria
                        if criterion.id not in selected
                    ],
                    sum(
                        criterion.priority
                        for criterion in criteria
                        if criterion.id in selected
                    )
                    / sum(criterion.priority for criterion in criteria),
                    {"percent": None, "calibrated": False, "status": "not_estimated"},
                )
            )

        models = [
            model
            for model in self.models
            if model.name in request.permissions.allowed_models
            and (request.model is None or request.model == model.name)
        ]
        for model in models:
            if self.policy == "per_criterion":
                calls = []
                for criterion in criteria:
                    call = make_call(
                        request, (criterion,), documents, model, "incremental"
                    )
                    if (
                        call is None
                        or sum(item.cost_high for item in calls) + call.cost_high
                        > request.budget.max_usd
                        or sum(item.seconds_high for item in calls) + call.seconds_high
                        > request.budget.max_seconds
                    ):
                        break
                    calls.append(call)
                if calls:
                    candidate(model, "incremental", calls)
                continue
            groups = (
                [criteria[:count] for count in range(len(criteria), 0, -1)]
                if self.policy == "budget_aware"
                else [criteria]
            )
            for group in groups:
                strategy = "single_pass"
                call = make_call(request, group, documents, model, strategy)
                if (
                    call is not None
                    and call.cost_high <= request.budget.max_usd
                    and call.seconds_high <= request.budget.max_seconds
                ):
                    candidate(model, strategy, [call])
                    break
        if not candidates:
            return Plan(
                None,
                "defer",
                [],
                [criterion.id for criterion in criteria],
                0,
                {"percent": None, "calibrated": False, "status": "no_feasible_plan"},
            )
        return min(
            candidates,
            key=lambda plan: (
                -plan.coverage,
                sum(call.cost_high for call in plan.calls),
                sum(call.seconds_high for call in plan.calls),
                plan.model.name,
            ),
        )


def prepare(
    suite_path,
    models_path,
    output,
    *,
    experiment="scheduling",
    model=None,
    repetitions=3,
    seed=1729,
    split=None,
):
    suite_path, output = suite_path.resolve(), output.resolve()
    suite = read_json(suite_path)
    if set(suite) != {"id", "description", "tasks", "conditions"}:
        raise ValueError("Suite needs id, description, tasks and conditions")
    identifier(suite["id"])
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("repetitions must be a positive integer")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if split not in {None, "development", "test"}:
        raise ValueError("split must be development or test")
    profiles = load_models(models_path)
    Planner(profiles)
    if experiment not in {"scheduling", "selection"}:
        raise ValueError("experiment must be scheduling or selection")
    if experiment == "scheduling":
        if model is None and len(profiles) != 1:
            raise ValueError(
                "Scheduling requires --model when profiles contain multiple models"
            )
        profiles = [
            profile for profile in profiles if model is None or profile.name == model
        ]
        if not profiles:
            raise ValueError("Unknown pinned model")
    elif model is not None:
        raise ValueError("Selection uses the complete approved model pool, not --model")
    if len({profile.provider == "copilot" for profile in profiles}) > 1:
        raise ValueError(
            "Separate Copilot usage-equivalent studies from direct-API pricing studies"
        )
    for profile in profiles:
        identifier(profile.name)
    tasks, snapshots, groups, source_splits = [], {}, {}, {}
    for entry in suite["tasks"]:
        if set(entry) != {
            "id",
            "family",
            "group",
            "split",
            "origin",
            "provenance",
            "goal",
            "sources",
            "criteria",
            "rubric",
        }:
            raise ValueError("Task fields must match the documented suite schema")
        task = dict(entry)
        for key in ("id", "family", "group"):
            identifier(task[key])
        if task["split"] not in {"development", "test"}:
            raise ValueError("Task split must be development or test")
        if (
            task["origin"] not in {"authored", "external"}
            or not task["provenance"].strip()
        ):
            raise ValueError(
                "Declare task origin and provenance, including document licensing"
            )
        if task["group"] in groups and groups[task["group"]] != task["split"]:
            raise ValueError("Related task groups cannot cross development/test splits")
        groups[task["group"]] = task["split"]
        criteria = tuple(Criterion(**item) for item in task["criteria"])
        if set(task["rubric"]) != {criterion.id for criterion in criteria} or not all(
            isinstance(value, str) and value.strip()
            for value in task["rubric"].values()
        ):
            raise ValueError("Every original criterion needs human-review guidance")
        paths = tuple((suite_path.parent / name).resolve() for name in task["sources"])
        request = Request(
            task["goal"],
            paths,
            criteria,
            Budget(1, 120),
            Permissions(
                tuple(path.parent for path in paths),
                output,
                tuple(p.name for p in profiles),
            ),
        )
        documents = load_documents(request)
        task["sources"] = []
        for index, document in enumerate(documents):
            grouping = (task["split"], task["group"])
            if (
                document.digest in source_splits
                and source_splits[document.digest] != grouping
            ):
                raise ValueError(
                    "Identical source documents must stay "
                    "in the same split and related group"
                )
            source_splits[document.digest] = grouping
            name = f"sources/{task['id']}/{index}{document.path.suffix.lower()}"
            snapshots[name] = document.text.encode("utf-8")
            task["sources"].append(name)
        tasks.append(task)
    if not tasks or len({task["id"] for task in tasks}) != len(tasks):
        raise ValueError("Supply nonempty tasks with unique IDs")
    if split is not None:
        tasks = [task for task in tasks if task["split"] == split]
        if not tasks:
            raise ValueError("No tasks match the requested split")
        sources = {name for task in tasks for name in task["sources"]}
        snapshots = {name: data for name, data in snapshots.items() if name in sources}
    conditions = suite["conditions"]
    if not conditions or len({condition["id"] for condition in conditions}) != len(
        conditions
    ):
        raise ValueError("Supply nonempty conditions with unique IDs")
    for condition in conditions:
        if set(condition) - {"id", "max_usd", "max_seconds", "change"}:
            raise ValueError("Unknown condition field")
        identifier(condition["id"])
        budget = Budget(condition["max_usd"], condition["max_seconds"])
        if "change" in condition:
            change = condition["change"]
            if set(change) != {"after_seconds", "max_usd", "max_seconds"}:
                raise ValueError(
                    "A change needs after_seconds, max_usd and max_seconds"
                )
            positive(change["after_seconds"], "after_seconds")
            updated = Budget(change["max_usd"], change["max_seconds"])
            if (
                updated.max_usd > budget.max_usd
                or updated.max_seconds >= budget.max_seconds
                or change["after_seconds"] >= budget.max_seconds
            ):
                raise ValueError(
                    "Changes must shorten the deadline without increasing the budget"
                )
    arms = [{"id": "spawn", "policy": "spawn", "models": [p.name for p in profiles]}]
    for policy in ("single_pass", "per_criterion"):
        arms.extend(
            {
                "id": f"{policy}:{profile.name}",
                "policy": policy,
                "models": [profile.name],
            }
            for profile in profiles
        )
    arms.append(
        {
            "id": "budget_aware",
            "policy": "budget_aware",
            "models": [p.name for p in profiles],
        }
    )
    rng = random.Random(seed)
    blocks = [
        (task["id"], condition["id"], repetition)
        for task in tasks
        for condition in conditions
        for repetition in range(repetitions)
    ]
    rng.shuffle(blocks)
    cases = []
    for task_id, condition_id, repetition in blocks:
        order = list(arms)
        rng.shuffle(order)
        for arm in order:
            cases.append(
                {
                    "id": f"case-{len(cases):06d}",
                    "review_id": f"review-{rng.getrandbits(128):032x}",
                    "task": task_id,
                    "condition": condition_id,
                    "repetition": repetition,
                    "arm": arm["id"],
                }
            )
    plan = {
        "version": 1,
        "suite": suite["id"],
        "description": suite["description"],
        "experiment": experiment,
        "split": split,
        "repetitions": repetitions,
        "seed": seed,
        "models": [asdict(profile) for profile in profiles],
        "tasks": tasks,
        "conditions": conditions,
        "arms": arms,
        "cases": cases,
        "implementation": implementation(),
        "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in snapshots.items()
        },
    }
    plan["sha256"] = digest(plan)
    output.mkdir(parents=True, exist_ok=False)
    for name, data in snapshots.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    write_json(output / "plan.json", plan)
    return {
        "plan_sha256": plan["sha256"],
        "cases": len(cases),
        "models": [profile.name for profile in profiles],
        "task_origins": sorted({task["origin"] for task in tasks}),
        "per_run_budget_sum_usd": sum(
            condition["max_usd"] * len(tasks) * repetitions * len(arms)
            for condition in conditions
        ),
        "warning": (
            "No inference. Authored fixtures and split labels "
            "do not establish representativeness."
        ),
    }


def frozen(output, *, check_code=False):
    plan = read_json(output / "plan.json")
    seal = plan.pop("sha256")
    if digest(plan) != seal:
        raise ValueError("Frozen plan was modified; prepare a new experiment")
    plan["sha256"] = seal
    for name, expected in plan["files"].items():
        path = (output / name).resolve()
        if (
            not path.is_relative_to(output.resolve())
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Frozen source snapshot was modified")
    if check_code and plan["implementation"] != implementation():
        raise ValueError(
            "Implementation changed after preparation; prepare a new experiment"
        )
    return plan


class SyntheticProvider:
    is_simulation = True

    def complete(self, model, call, timeout):
        return Response(
            '{"findings": []}',
            0,
            0,
            reported_cost_usd=0,
            billing={"basis": "synthetic"},
        )


class Meter:
    def __init__(self, provider, max_usd, max_calls, journal=None):
        self.provider, self.max_usd, self.max_calls = provider, max_usd, max_calls
        self.is_simulation = bool(getattr(provider, "is_simulation", False))
        self.calls, self.cost, self.halted = [], 0.0, None
        self.journal, self.case_id = journal, None

    def record(self, event, item):
        if self.journal is not None:
            with self.journal.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps({"event": event, **item}, allow_nan=False) + "\n"
                )
                stream.flush()

    def complete(self, model, call, timeout):
        if self.halted:
            raise ProviderError(self.halted)
        if (
            len(self.calls) >= self.max_calls
            or self.cost + call.cost_high > self.max_usd
        ):
            self.halted = "Aggregate study limit; no new provider call started"
            raise ProviderError(self.halted)
        item = {
            "call_index": len(self.calls),
            "case_id": self.case_id,
            "model": model.name,
            "criteria": list(call.criteria),
            "prompt": json.loads(call.prompt),
            "billing_known": False,
            "reserved_usd": call.cost_high,
        }
        self.record("reserved", item)
        self.calls.append(item)
        prior = self.cost
        self.cost = prior + call.cost_high
        started = time.monotonic()
        try:
            response = self.provider.complete(model, call, timeout)
        except BaseException:
            self.halted = (
                "Provider call failed or interrupted; billing unknown; study stopped"
            )
            item.update({"billing_known": False, "reserved_usd": call.cost_high})
            item["elapsed_seconds"] = time.monotonic() - started
            self.record("failed", item)
            raise
        finally:
            item["elapsed_seconds"] = time.monotonic() - started
        known = response.reported_cost_usd is not None or (
            model.provider != "copilot"
            and response.input_tokens is not None
            and response.output_tokens is not None
        )
        actual = response.reported_cost_usd
        if actual is None:
            actual = (
                model.cost(response.input_tokens, response.output_tokens)
                if known
                else call.cost_high
            )
        self.cost = prior + actual
        item.update(
            {
                "response": asdict(response),
                "billing_known": known,
                "accounted_usd": actual,
            }
        )
        if not known:
            self.halted = "Incomplete billing; reservation retained; study stopped"
        elif actual > call.cost_high + 1e-12 or self.cost > self.max_usd + 1e-12:
            self.halted = (
                "Reported cost exceeded reservation or study cap; study stopped"
            )
        self.record("returned", item)
        return response


def execute(
    output,
    *,
    live=False,
    approve_sharing=False,
    max_usd=None,
    max_calls=None,
    provider=None,
):
    output = output.resolve()
    plan = frozen(output, check_code=True)
    if live and os.environ.get("SPAWN_ACTIVE") == "1":
        raise ValueError("Nested paid Spawn execution is not permitted")
    if live and (not approve_sharing or max_usd is None or max_calls is None):
        raise ValueError(
            "Live execution requires --approve-sharing, --max-total-usd and --max-calls"
        )
    positive(max_usd if max_usd is not None else 0, "max_total_usd", zero=True)
    if max_calls is not None and (type(max_calls) is not int or max_calls < 1):
        raise ValueError("max_calls must be a positive integer")
    if provider is not None and not live:
        raise ValueError("Custom providers require explicit live-mode authorization")
    backend = (
        provider
        if provider is not None
        else HTTPProvider()
        if live
        else SyntheticProvider()
    )
    meter = Meter(
        backend,
        max_usd if max_usd is not None else 1e12,
        max_calls or 1_000_000,
        journal=output / "provider-calls.jsonl",
    )
    start_record = {
        "plan_sha256": plan["sha256"],
        "simulation": meter.is_simulation,
        "max_total_usd": max_usd,
        "max_calls": max_calls,
        "environment": environment(),
        "started_unix": time.time(),
    }
    with (output / "started.json").open("x", encoding="utf-8") as stream:
        json.dump(start_record, stream, indent=2)
    tasks = {task["id"]: task for task in plan["tasks"]}
    conditions = {condition["id"]: condition for condition in plan["conditions"]}
    arms = {arm["id"]: arm for arm in plan["arms"]}
    models = {entry["name"]: Model(**entry) for entry in plan["models"]}
    completed = 0
    with (output / "records.jsonl").open("x", encoding="utf-8") as stream:
        for case in plan["cases"]:
            started = time.monotonic()
            task, condition, arm = (
                tasks[case["task"]],
                conditions[case["condition"]],
                arms[case["arm"]],
            )
            selected = [models[name] for name in arm["models"]]
            meter.case_id = case["id"]
            planner = (
                Planner(selected)
                if arm["policy"] == "spawn"
                else Baseline(selected, arm["policy"])
            )
            directory = output / "runs" / case["id"]
            directory.mkdir(parents=True)
            control = directory / "control.json"
            budget = {key: condition[key] for key in ("max_usd", "max_seconds")}
            write_json(control, budget)
            request = Request(
                task["goal"],
                tuple(output / name for name in task["sources"]),
                tuple(Criterion(**item) for item in task["criteria"]),
                Budget(**budget),
                Permissions(
                    (output / "sources", directory),
                    directory / "artifacts",
                    tuple(arm["models"]),
                    True,
                ),
                task_class=task["family"],
                control_file=control,
            )
            offset = len(meter.calls)
            stop, changed = threading.Event(), {}
            timer = None
            if "change" in condition:
                change = condition["change"]

                def tighten(
                    change=change,
                    control=control,
                    stop=stop,
                    changed=changed,
                    started=started,
                    budget=budget,
                ):
                    if not stop.wait(
                        max(0, change["after_seconds"] - (time.monotonic() - started))
                    ):
                        try:
                            temporary = control.with_suffix(".tmp")
                            write_json(temporary, {key: change[key] for key in budget})
                            temporary.replace(control)
                            changed["at_seconds"] = time.monotonic() - started
                        except OSError as failure:
                            changed["error"] = str(failure)
                            meter.halted = "Deadline control failed; study stopped"

                timer = threading.Thread(target=tighten)
                timer.start()
            result, error = None, None
            try:
                result = Runtime(planner, meter).run(request)
            except Exception as failure:
                error = f"{type(failure).__name__}: {failure}"
            finally:
                stop.set()
                if timer is not None:
                    timer.join()
            elapsed = time.monotonic() - started
            effective = read_json(control)
            calls = meter.calls[offset:]
            billing_known = all(call["billing_known"] for call in calls)
            cost = sum(
                call.get("accounted_usd", call.get("reserved_usd", 0)) for call in calls
            )
            within = (
                elapsed <= effective["max_seconds"]
                and cost <= effective["max_usd"] + 1e-12
            )
            preflight = (
                read_json(Path(result["artifacts"]) / "preflight.json")
                if result
                else None
            )
            record = {
                **case,
                "plan_sha256": plan["sha256"],
                "simulation": meter.is_simulation,
                "preflight": preflight,
                "result": result,
                "execution_error": error,
                "calls": calls,
                "accounted_cost_usd": cost,
                "billing_known": billing_known,
                "end_to_end_seconds": elapsed,
                "effective_limits": effective,
                "change_applied": changed,
                "within_limits": within,
                "study_halt": meter.halted,
            }
            stream.write(json.dumps(record, allow_nan=False) + "\n")
            stream.flush()
            completed += 1
            if (
                meter.halted
                or error
                or (result and result["stopping_reason"] == "interrupted")
            ):
                break
    status = {
        **start_record,
        "executed_cases": completed,
        "planned_cases": len(plan["cases"]),
        "complete": completed == len(plan["cases"]),
        "provider_calls": len(meter.calls),
        "accounted_cost_usd": meter.cost,
        "halt_reason": meter.halted,
        "records_sha256": hashlib.sha256(
            (output / "records.jsonl").read_bytes()
        ).hexdigest(),
    }
    write_json(output / "execution.json", status)
    return status


def records_for(output, plan):
    path = output / "records.jsonl"
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    cases = {case["id"]: case for case in plan["cases"]}
    started = read_json(output / "started.json")
    if started["plan_sha256"] != plan["sha256"]:
        raise ValueError("Execution started against a different frozen plan")
    seen = set()
    for record in records:
        cid = record["id"]
        if cid in seen or cid not in cases or record["plan_sha256"] != plan["sha256"]:
            raise ValueError("Duplicate, unknown or mismatched execution record")
        if any(record[key] != value for key, value in cases[cid].items()):
            raise ValueError("Execution record does not match the frozen case")
        if record["simulation"] != started["simulation"]:
            raise ValueError("Synthetic/live execution metadata does not match")
        seen.add(cid)
    if (output / "execution.json").exists():
        status = read_json(output / "execution.json")
        if hashlib.sha256(path.read_bytes()).hexdigest() != status["records_sha256"]:
            raise ValueError("Execution records changed after completion")
    return records


def packet_for(plan, records, output):
    tasks = {task["id"]: task for task in plan["tasks"]}
    items = []
    for record in records:
        task = tasks[record["task"]]
        items.append(
            {
                "review_id": record["review_id"],
                "goal": task["goal"],
                "criteria": task["criteria"],
                "rubric": task["rubric"],
                "sources": [
                    {
                        "id": f"S{index + 1}",
                        "text": (output / name).read_text(encoding="utf-8"),
                    }
                    for index, name in enumerate(task["sources"])
                ],
                "findings": record["result"]["findings"] if record["result"] else [],
                "judgments": {
                    criterion["id"]: dict.fromkeys(JUDGMENTS)
                    for criterion in task["criteria"]
                },
                "usefulness": None,
                "notes": "",
            }
        )
    random.Random(plan["seed"] + 1).shuffle(items)
    return {
        "instructions": (
            "Judge the full original task, including unanswered/deferred criteria. "
            "For unsupported questions, judge correct abstention using the rubric. "
            "Set correct/supported/complete to booleans; leave unreviewed values null. "
            "Usefulness: 0 unusable, 1 minor, 2 partial, "
            "3 mostly useful, 4 fully useful. "
            "No policy, model, timing or billing metadata is included. "
            "Reviewers must not access the experiment directory. "
            "Resolve multiple-rater "
            "disagreements separately and submit one adjudicated packet."
        ),
        "reviewer_alias": "",
        "simulation": read_json(output / "started.json")["simulation"],
        "items": items,
    }


def review_grades(plan, records, output, reviews):
    if reviews is None:
        return {}
    packet = read_json(reviews)
    expected = packet_for(plan, records, output)
    if set(packet) != set(expected) or not isinstance(packet["reviewer_alias"], str):
        raise ValueError("Use the exported review packet")
    if (
        packet["instructions"] != expected["instructions"]
        or packet["simulation"] != expected["simulation"]
    ):
        raise ValueError("Review packet metadata changed")
    if not packet["reviewer_alias"].strip():
        raise ValueError("Supply a non-identifying reviewer alias")
    originals = {item["review_id"]: item for item in expected["items"]}
    grades, seen = {}, set()
    for item in packet["items"]:
        rid = item["review_id"]
        if rid not in originals or rid in seen:
            raise ValueError("Unknown or duplicate blinded review ID")
        seen.add(rid)
        original = originals[rid]
        if set(item) != set(original) or any(
            item[key] != original[key]
            for key in original
            if key not in {"judgments", "usefulness", "notes"}
        ):
            raise ValueError(
                "Review content changed; only judgments, usefulness "
                "and notes are editable"
            )
        judgments = item["judgments"]
        if set(judgments) != set(original["judgments"]):
            raise ValueError(
                "Review every original criterion, not only returned findings"
            )
        values = []
        for criterion in item["criteria"]:
            judgment = judgments[criterion["id"]]
            if set(judgment) != set(JUDGMENTS) or any(
                value is not None and type(value) is not bool
                for value in judgment.values()
            ):
                raise ValueError("Judgments must be boolean or null")
            values.append(None if None in judgment.values() else all(judgment.values()))
        usefulness = item["usefulness"]
        if usefulness is not None and (
            type(usefulness) is not int or not 0 <= usefulness <= 4
        ):
            raise ValueError("Usefulness must be an integer from 0 to 4, or null")
        if None not in values and usefulness is not None:
            weight = sum(criterion.get("priority", 1) for criterion in item["criteria"])
            grades[rid] = {
                "quality_pass": all(values),
                "weighted_completion": sum(
                    criterion.get("priority", 1) * passed
                    for criterion, passed in zip(item["criteria"], values, strict=True)
                )
                / weight,
                "usefulness": usefulness,
            }
    return grades


def summarize(output, reviews=None, *, bootstrap_samples=2000):
    if type(bootstrap_samples) is not int or bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be a positive integer")
    output = output.resolve()
    plan = frozen(output)
    records = records_for(output, plan)
    grades = review_grades(plan, records, output, reviews)
    started = read_json(output / "started.json")
    simulation = started["simulation"]
    tasks = {task["id"]: task for task in plan["tasks"]}
    observed = {record["id"]: record for record in records}
    groups, results = {}, []
    for case in plan["cases"]:
        key = (tasks[case["task"]]["split"], case["condition"], case["arm"])
        groups.setdefault(key, []).append(case)
    for (split, condition, arm), cases in groups.items():
        rows = [observed[case["id"]] for case in cases if case["id"] in observed]
        graded = [row for row in rows if row["review_id"] in grades]
        valid = len(graded) == len(cases) and not simulation
        successes = sum(
            grades[row["review_id"]]["quality_pass"]
            and row["within_limits"]
            and row["billing_known"]
            and not row["execution_error"]
            and not row["study_halt"]
            for row in graded
        )
        forecasted = [
            row for row in rows if row["preflight"] and row["preflight"]["feasible"]
        ]
        priced = [row for row in forecasted if row["billing_known"]]
        total_cost = sum(row["accounted_cost_usd"] for row in rows)
        results.append(
            {
                "split": split,
                "condition": condition,
                "arm": arm,
                "planned_runs": len(cases),
                "executed_runs": len(rows),
                "reviewed_runs": len(graded),
                "primary_success_rate": successes / len(cases) if valid else None,
                "quality_pass_rate": statistics.mean(
                    grades[row["review_id"]]["quality_pass"] for row in graded
                )
                if valid
                else None,
                "weighted_completion": statistics.mean(
                    grades[row["review_id"]]["weighted_completion"] for row in graded
                )
                if valid
                else None,
                "mean_usefulness": statistics.mean(
                    grades[row["review_id"]]["usefulness"] for row in graded
                )
                if valid
                else None,
                "total_accounted_usd": total_cost,
                "cost_includes_reservations": any(
                    not row["billing_known"] for row in rows
                ),
                "cost_per_success_usd": total_cost / successes
                if valid and successes
                else None,
                "mean_end_to_end_seconds": statistics.mean(
                    row["end_to_end_seconds"] for row in rows
                )
                if rows
                else None,
                "limit_misses": sum(not row["within_limits"] for row in rows),
                "execution_errors": sum(
                    bool(row["execution_error"] or row["study_halt"]) for row in rows
                ),
                "provider_calls": sum(len(row["calls"]) for row in rows),
                "forecasted_runs": len(forecasted),
                "priced_forecasted_runs": len(priced),
                "time_scenario_coverage": statistics.mean(
                    row["preflight"]["estimated_seconds_range"][0]
                    <= row["end_to_end_seconds"]
                    <= row["preflight"]["estimated_seconds_range"][1]
                    for row in forecasted
                )
                if forecasted
                else None,
                "cost_scenario_coverage": statistics.mean(
                    row["preflight"]["estimated_cost_usd_range"][0]
                    <= row["accounted_cost_usd"]
                    <= row["preflight"]["estimated_cost_usd_range"][1]
                    for row in priced
                )
                if priced
                else None,
            }
        )
    paired = []
    if not simulation:
        for split, condition, arm in groups:
            if arm == "spawn":
                continue
            left = groups.get((split, condition, "spawn"), [])
            right = groups[(split, condition, arm)]
            paired_cases = {(case["task"], case["repetition"]): case for case in right}
            clusters = {}
            ready = len(left) == len(right)
            for case in left:
                other = paired_cases[(case["task"], case["repetition"])]
                pair = [observed.get(item["id"]) for item in (case, other)]
                if any(
                    row is None
                    or row["review_id"] not in grades
                    or not row["billing_known"]
                    for row in pair
                ):
                    ready = False
                    break
                metrics = []
                for row in pair:
                    grade = grades[row["review_id"]]
                    metrics.append(
                        [
                            float(
                                grade["quality_pass"]
                                and row["within_limits"]
                                and not row["execution_error"]
                                and not row["study_halt"]
                            ),
                            grade["weighted_completion"],
                            row["accounted_cost_usd"],
                            row["end_to_end_seconds"],
                        ]
                    )
                clusters.setdefault(tasks[case["task"]]["group"], []).append(
                    [a - b for a, b in zip(*metrics, strict=True)]
                )
            if ready and clusters:
                units = [
                    [statistics.mean(values) for values in zip(*cluster, strict=True)]
                    for cluster in clusters.values()
                ]
                rng = random.Random(plan["seed"])
                samples = [
                    [
                        statistics.mean(values)
                        for values in zip(
                            *rng.choices(units, k=len(units)), strict=True
                        )
                    ]
                    for _ in range(bootstrap_samples)
                ]
                intervals = {}
                for index, name in enumerate(
                    (
                        "primary_success_rate",
                        "weighted_completion",
                        "cost_usd",
                        "seconds",
                    )
                ):
                    values = sorted(sample[index] for sample in samples)
                    intervals[name] = {
                        "spawn_minus_baseline": statistics.mean(
                            unit[index] for unit in units
                        ),
                        "cluster_bootstrap_95_percentile": [
                            values[int(0.025 * (len(values) - 1))],
                            values[int(0.975 * (len(values) - 1))],
                        ]
                        if len(units) >= 2
                        else None,
                    }
                paired.append(
                    {
                        "split": split,
                        "condition": condition,
                        "baseline": arm,
                        "independent_groups_declared": len(units),
                        "metrics": intervals,
                    }
                )
    summary = {
        "plan_sha256": plan["sha256"],
        "simulation": simulation,
        "cost_units": (
            "synthetic_not_measured"
            if simulation
            else "copilot_usage_equivalent_usd"
            if plan["models"][0]["provider"] == "copilot"
            else "configured_token_price_usd"
        ),
        "representativeness": "Not established by the harness or by test-split labels.",
        "complete_execution": len(records) == len(plan["cases"]),
        "complete_review": len(grades) == len(plan["cases"]),
        "primary_metric": (
            "Full original task correct, supported and complete "
            "within both final limits; billing known."
        ),
        "paired_estimator": (
            "Equal-weighted declared groups, averaging repetitions and tasks "
            "within each group; not necessarily the pooled run-rate difference."
        ),
        "task_origins": sorted({task["origin"] for task in plan["tasks"]}),
        "groups": results,
        "paired_comparisons": paired,
        "warning": (
            "Synthetic runs cannot establish model performance. "
            "Bootstrap intervals require independent groups and adequate sample size."
        ),
    }
    write_json(output / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser(
        "prepare",
        help="Freeze a suite, sources, profiles and randomized schedule; no inference",
    )
    build.add_argument("--suite", type=Path, required=True)
    build.add_argument("--models", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument(
        "--experiment", choices=("scheduling", "selection"), default="scheduling"
    )
    build.add_argument("--model")
    build.add_argument("--repetitions", type=int, default=3)
    build.add_argument("--seed", type=int, default=1729)
    build.add_argument("--split", choices=("development", "test"))
    run = commands.add_parser(
        "run",
        help="Synthetic by default; live requires sharing approval and study caps",
    )
    run.add_argument("--live", action="store_true")
    run.add_argument("--approve-sharing", action="store_true")
    run.add_argument("--max-total-usd", type=float)
    run.add_argument("--max-calls", type=int)
    export = commands.add_parser(
        "export-review",
        help="Export content and blank judgments without policy/cost metadata",
    )
    export.add_argument("--packet", type=Path, required=True)
    analyze = commands.add_parser(
        "summarize", help="Analyze execution and optional adjudicated human reviews"
    )
    analyze.add_argument("--reviews", type=Path)
    for command in (run, export, analyze):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(
                args.suite,
                args.models,
                args.output,
                experiment=args.experiment,
                model=args.model,
                repetitions=args.repetitions,
                seed=args.seed,
                split=args.split,
            )
        elif args.command == "run":
            result = execute(
                args.output,
                live=args.live,
                approve_sharing=args.approve_sharing,
                max_usd=args.max_total_usd,
                max_calls=args.max_calls,
            )
        elif args.command == "export-review":
            output = args.output.resolve()
            plan = frozen(output)
            packet = packet_for(plan, records_for(output, plan), output)
            with args.packet.open("x", encoding="utf-8") as stream:
                json.dump(packet, stream, indent=2)
            result = {"packet": str(args.packet), "items": len(packet["items"])}
        else:
            result = summarize(args.output, args.reviews)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 3 if args.command == "run" and not result["complete"] else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Benchmark error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
