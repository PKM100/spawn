from __future__ import annotations

import json
import math
from dataclasses import asdict

from .contracts import SYSTEM, Call, Criterion, Document, Model, Plan, Request
from .documents import select_excerpts
from .history import History, cohort_key

WARNINGS = [
    "Cost and time ranges are profile-based scenarios, "
    "not calibrated prediction intervals.",
    "Token reservation assumes <=1 token per UTF-8 byte plus 2048 protocol tokens; "
    "custom models/providers may violate this assumption.",
    "Coverage counts prioritized criteria scheduled, not probability of task success.",
    "Exact-quote checks do not prove that an answer is correct "
    "or supported by its quotes.",
    "Automatic selection maximizes planned coverage, then prefers configured models "
    "and lower estimated cost/time; it does not establish a globally optimal strategy.",
]


def make_call(
    request: Request,
    criteria: tuple[Criterion, ...],
    documents: list[Document],
    model: Model,
    strategy: str,
) -> Call | None:
    excerpts = select_excerpts(
        documents,
        request.goal + " " + " ".join(c.description for c in criteria),
        chars_per_source=6000 if strategy == "single_pass" else 2400,
    )
    prompt = json.dumps(
        {
            "goal": request.goal,
            "criteria": [asdict(c) for c in criteria],
            "documents": [
                {"source_id": key, "text": text} for key, text in excerpts.items()
            ],
        },
        ensure_ascii=False,
    )
    byte_count = len((SYSTEM + prompt).encode("utf-8"))
    upper = byte_count + 2048
    expected_input = math.ceil(byte_count / 3) + 256
    output = 750 * len(criteria)
    if (
        output > model.max_output_tokens
        or upper + output > model.context_tokens
        or any(c.min_sources > len(documents) for c in criteria)
    ):
        return None
    expected_seconds = (
        model.latency_seconds
        + expected_input / model.input_tokens_per_second
        + output / model.output_tokens_per_second
    )
    return Call(
        tuple(c.id for c in criteria),
        prompt,
        excerpts,
        output,
        upper,
        model.cost(expected_input, math.ceil(output * 0.4)),
        max(model.cost(upper, output), model.per_call_usd_reserve),
        expected_seconds * 0.5,
        expected_seconds * 1.8,
    )


class Planner:
    def __init__(self, models: list[Model], history: History | None = None):
        if not models or len({m.name for m in models}) != len(models):
            raise ValueError("Supply uniquely named model profiles")
        self.models = models
        self.history = history

    def plan(self, request: Request, documents: list[Document]) -> Plan:
        allowed = [
            m
            for m in self.models
            if m.name in request.permissions.allowed_models
            and (request.model is None or m.name == request.model)
        ]
        if not allowed:
            raise PermissionError("No approved model matches the requested model")
        criteria = tuple(sorted(request.criteria, key=lambda c: -c.priority))
        weight = sum(c.priority for c in criteria)
        schedulable = (
            tuple(c for c in criteria if c.min_sources <= len(documents))
            if request.allow_scope_reduction
            else criteria
        )
        source_bytes = sum(len(d.text.encode("utf-8")) for d in documents)
        candidates = []
        for model in allowed:
            for strategy in ("single_pass", "incremental"):
                counts = (
                    range(len(schedulable), 0, -1)
                    if request.allow_scope_reduction
                    else (len(criteria),)
                )
                for count in counts:
                    selected = schedulable[:count]
                    groups = (
                        (selected,)
                        if strategy == "single_pass"
                        else tuple((c,) for c in selected)
                    )
                    calls = [
                        make_call(request, group, documents, model, strategy)
                        for group in groups
                    ]
                    if any(call is None for call in calls):
                        continue
                    cost = sum(call.cost_high for call in calls)
                    seconds = sum(call.seconds_high for call in calls)
                    if (
                        cost > request.budget.max_usd
                        or seconds > request.budget.max_seconds
                    ):
                        continue
                    probability = (
                        self.history.forecast(
                            cohort_key(request, model, strategy, source_bytes)
                        )
                        if self.history
                        else {
                            "percent": None,
                            "status": "insufficient_evidence",
                            "samples": 0,
                            "calibrated": False,
                        }
                    )
                    selected_ids = {c.id for c in selected}
                    deferred = [c.id for c in criteria if c.id not in selected_ids]
                    if deferred:
                        probability = {
                            **probability,
                            "percent": None,
                            "interval_percent": None,
                            "status": "original_contract_not_fully_scheduled",
                        }
                    plan = Plan(
                        model,
                        strategy,
                        calls,
                        deferred,
                        sum(c.priority for c in selected) / weight,
                        probability,
                        warnings=list(WARNINGS)
                        + (
                            [
                                "Copilot costs are usage equivalents, not invoice "
                                "spend. Its session cap is soft/postpaid; output "
                                "length is requested, not hard-enforced."
                            ]
                            if model.provider == "copilot"
                            else []
                        ),
                    )
                    candidates.append(plan)
                    break
        if not candidates:
            return Plan(
                None,
                "defer",
                [],
                [c.id for c in criteria],
                0,
                {
                    "percent": None,
                    "status": "no_feasible_plan",
                    "calibrated": False,
                },
                warnings=[
                    *WARNINGS,
                    "No candidate fits the configured budget, deadline, source "
                    "requirements and context limits. No model request will be sent.",
                ],
            )
        candidates.sort(
            key=lambda p: (
                -p.coverage,
                -p.model.preference,
                sum(c.cost_high for c in p.calls),
                sum(c.seconds_high for c in p.calls),
            )
        )
        best = candidates[0]
        best.alternatives = [
            {
                "model": p.model.name,
                "strategy": p.strategy,
                "planned_priority_coverage_percent": round(p.coverage * 100, 2),
                "cost_usd_upper_scenario": sum(c.cost_high for c in p.calls),
                "seconds_upper_scenario": sum(c.seconds_high for c in p.calls),
            }
            for p in candidates[1:]
        ]
        return best
