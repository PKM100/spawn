from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace
from pathlib import Path

from .contracts import Budget, Document, Request
from .documents import load_documents
from .evidence import validate_findings
from .history import History, cohort_key
from .planner import Planner
from .providers import HTTPProvider, ProviderError


def read_control(request: Request, current: Budget) -> Budget:
    if request.control_file is None:
        return current
    path = request.permissions.readable(Path(request.control_file))
    with path.open("rb") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("Control file exceeds 4096 bytes")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) - {"max_usd", "max_seconds"}:
        raise ValueError("Control file accepts only max_usd and max_seconds")
    updated = Budget(
        data.get("max_usd", current.max_usd),
        data.get("max_seconds", current.max_seconds),
    )
    return Budget(
        min(updated.max_usd, current.max_usd),
        min(updated.max_seconds, current.max_seconds),
    )


def render_report(result: dict, documents: list[Document], request: Request) -> str:
    lines = [
        "# Spawn research report",
        "",
        request.goal,
        "",
        f"Status: **{result['status']}**",
        f"Exact-quote evidence coverage: {result['evidence_coverage_percent']:.1f}%.",
        "Coverage is not independently verified correctness or success probability.",
        "",
    ]
    descriptions = {c.id: c.description for c in request.criteria}
    for finding in result["findings"]:
        lines += [
            f"## {descriptions[finding['criterion_id']]}",
            "",
            finding["answer"],
            "",
        ]
        for evidence in finding["evidence"]:
            quote = "\n".join("> " + line for line in evidence["quote"].splitlines())
            lines += [f"{quote} [{evidence['source_id']}]", ""]
    if result["deferred"]:
        lines += ["## Deferred work", ""]
        lines += [f"- {descriptions[cid]}" for cid in result["deferred"]]
        lines.append("")
    lines += ["## Sources", ""]
    lines += [f"- [{d.id}] {d.path.name} (SHA-256: `{d.digest}`)" for d in documents]
    if result["errors"]:
        lines += ["", "## Execution notices", ""]
        lines += [f"- {error}" for error in result["errors"]]
    return "\n".join(lines) + "\n"


class Runtime:
    def __init__(
        self,
        planner: Planner,
        provider=None,
        *,
        clock=time.monotonic,
        on_event=None,
    ):
        self.planner = planner
        self.provider = provider if provider is not None else HTTPProvider()
        self.clock = clock
        self.on_event = on_event

    def run(self, request: Request) -> dict:
        started = self.clock()
        documents = load_documents(request)
        if not request.permissions.send_documents:
            raise PermissionError(
                "send_documents=true is required to send excerpts to approved models"
            )
        limits = read_control(request, request.budget)
        initial = self.planner.plan(replace(request, budget=limits), documents)
        root = request.permissions.output_root
        root.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        directory = root / run_id
        directory.mkdir()
        (directory / "preflight.json").write_text(
            json.dumps(initial.public(), indent=2), encoding="utf-8"
        )
        findings = []
        errors = []
        attempted = set()
        accounted_cost = 0.0
        all_usage_reported = True
        billing_bases = set()
        models_used = set()
        strategies_used = set()
        stopping_reason = "no_feasible_plan"

        def emit(event: dict) -> None:
            event["elapsed_seconds"] = max(0, self.clock() - started)
            with (directory / "trace.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")
            if self.on_event:
                self.on_event(event)

        emit({"type": "started", "plan": initial.public()})
        while initial.feasible:
            elapsed = self.clock() - started
            pending = tuple(c for c in request.criteria if c.id not in attempted)
            if not pending:
                stopping_reason = "all_scheduled_criteria_attempted"
                break
            try:
                limits = read_control(request, limits)
            except (OSError, ValueError, PermissionError) as error:
                errors.append(f"Control update rejected: {error}")
                stopping_reason = "invalid_control"
                break
            remaining_time = limits.max_seconds - elapsed
            remaining_money = limits.max_usd - accounted_cost
            if remaining_time <= 0 or remaining_money < 0:
                stopping_reason = "deadline_or_budget_exhausted"
                break
            remaining = replace(
                request,
                criteria=pending,
                budget=Budget(remaining_money, remaining_time),
            )
            plan = self.planner.plan(remaining, documents)
            emit({"type": "replanned", "plan": plan.public()})
            if not plan.feasible:
                stopping_reason = "remaining_work_does_not_fit"
                break
            model, call = plan.model, plan.calls[0]
            attempted.update(call.criteria)
            models_used.add(model.name)
            strategies_used.add(plan.strategy)
            accounted_cost += call.cost_high
            emit(
                {
                    "type": "call_reserved",
                    "model": model.name,
                    "strategy": plan.strategy,
                    **call.public(),
                }
            )
            try:
                response = self.provider.complete(model, call, remaining_time)
            except KeyboardInterrupt:
                all_usage_reported = False
                errors.append("Interrupted; the in-flight call may still be billed")
                stopping_reason = "interrupted"
                break
            except (ProviderError, TimeoutError) as error:
                all_usage_reported = False
                errors.append(str(error))
                emit({"type": "call_failed", "reason": str(error)})
                if request.on_failure == "abort":
                    stopping_reason = "provider_failure"
                    break
                continue
            usage_known = response.reported_cost_usd is not None or (
                model.provider != "copilot"
                and response.input_tokens is not None
                and response.output_tokens is not None
            )
            actual_cost = (
                response.reported_cost_usd
                if response.reported_cost_usd is not None
                else (
                    model.cost(response.input_tokens, response.output_tokens)
                    if usage_known
                    else call.cost_high
                )
            )
            billing_bases.add(response.billing.get("basis", "reported_tokens"))
            accounted_cost += actual_cost - call.cost_high
            all_usage_reported &= usage_known
            emit(
                {
                    "type": "call_returned",
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "accounted_usd": actual_cost,
                    "usage_reported": usage_known,
                    "billing": response.billing,
                }
            )
            try:
                if response.truncated:
                    raise ValueError("Provider output was truncated")
                accepted, rejected = validate_findings(
                    response.text, call, request.criteria, documents
                )
                findings.extend(accepted)
                errors.extend(rejected)
            except (ValueError, TypeError, KeyError, IndexError) as error:
                accepted = []
                errors.append(f"Invalid research output: {error}")
            emit(
                {
                    "type": "evidence_checked",
                    "accepted_criteria": [f["criterion_id"] for f in accepted],
                }
            )
            if actual_cost > call.cost_high + 1e-12:
                errors.append(
                    "Provider usage exceeded the configured reservation assumptions"
                )
                stopping_reason = "reservation_exceeded"
                break
            if request.on_failure == "abort" and len(accepted) < len(call.criteria):
                stopping_reason = "evidence_failure"
                break
        try:
            limits = read_control(request, limits)
        except (OSError, ValueError, PermissionError) as error:
            errors.append(f"Final control update rejected: {error}")
            stopping_reason = "invalid_control"
        completed = {f["criterion_id"] for f in findings}
        elapsed = max(0, self.clock() - started)
        within_limits = (
            elapsed <= limits.max_seconds and accounted_cost <= limits.max_usd + 1e-12
        )
        if not within_limits:
            errors.append(
                "The run exceeded a current limit; network deadlines and "
                "provider billing bounds are best-effort, not guarantees"
            )
        deferred = [c.id for c in request.criteria if c.id not in completed]
        status = (
            "evidence_ready" if not deferred else "partial" if findings else "blocked"
        )
        result = {
            "run_id": run_id,
            "simulation": bool(getattr(self.provider, "is_simulation", False)),
            "status": status,
            "stopping_reason": stopping_reason,
            "evidence_coverage_percent": 100
            * sum(c.priority for c in request.criteria if c.id in completed)
            / sum(c.priority for c in request.criteria),
            "success_verified": False,
            "findings": findings,
            "deferred": deferred,
            "accounted_cost_usd": accounted_cost,
            "cost_basis": "+".join(sorted(billing_bases)) or "reported_tokens"
            if all_usage_reported
            else "includes_reservations",
            "elapsed_seconds": elapsed,
            "within_limits": within_limits,
            "final_limits": {
                "max_usd": limits.max_usd,
                "max_seconds": limits.max_seconds,
            },
            "models_used": sorted(models_used),
            "errors": errors,
            "artifacts": str(directory),
            "sources": [
                {"id": d.id, "path": str(d.path), "sha256": d.digest} for d in documents
            ],
        }
        emit({"type": "finished", "status": status, "within_limits": within_limits})
        (directory / "result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        (directory / "report.md").write_text(
            render_report(result, documents, request), encoding="utf-8"
        )
        history = self.planner.history or History(root / "history.sqlite3")
        cohort = "not_comparable"
        if (
            initial.model
            and models_used == {initial.model.name}
            and strategies_used == {initial.strategy}
            and limits == request.budget
            and not initial.deferred
        ):
            cohort = cohort_key(
                request,
                initial.model,
                initial.strategy,
                sum(len(d.text.encode("utf-8")) for d in documents),
            )
        history.record(
            run_id,
            cohort,
            accounted_cost,
            elapsed,
            eligible=not deferred
            and within_limits
            and all_usage_reported
            and stopping_reason != "invalid_control"
            and not result["simulation"],
        )
        return result
