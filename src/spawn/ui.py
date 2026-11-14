from __future__ import annotations

import time

from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from .contracts import Plan, Request

THEME = Theme(
    {
        "accent": "bold cyan",
        "muted": "dim",
        "good": "green",
        "warn": "yellow",
        "bad": "red",
    }
)


def make_console() -> Console:
    return Console(theme=THEME, highlight=False)


def money(value: float) -> str:
    return f"${value:.4f}" if value >= 0.01 else f"${value:.6f}"


def duration(seconds: float) -> str:
    return f"{seconds:.1f}s" if seconds < 60 else f"{seconds / 60:.1f}m"


def heading(console: Console, subtitle: str = "Know the tradeoff. Then spawn.") -> None:
    console.print()
    console.print(Text("  S P A W N", style="bold cyan"))
    console.print(Text("  " + subtitle, style="dim"))
    console.print()


def _metric(title: str, value: str, note: str, style: str = "cyan") -> Panel:
    return Panel(
        Group(Text(value, style=f"bold {style}"), Text(note, style="dim")),
        title=title,
        title_align="left",
        border_style="bright_black",
        padding=(1, 2),
    )


def _criteria_table(
    request: Request, planned: set[str], completed=None, active=None
) -> Table:
    completed = completed or set()
    active = active or set()
    table = Table(box=box.SIMPLE, expand=True, padding=(0, 1))
    table.add_column("STATE", width=13, no_wrap=True)
    table.add_column("DELIVERABLE", ratio=1, overflow="fold")
    table.add_column("PRIORITY", justify="right", width=8)
    for criterion in sorted(request.criteria, key=lambda c: -c.priority):
        if criterion.id in completed:
            label, style = "EVIDENCE", "green"
        elif criterion.id in active:
            label, style = "WORKING", "cyan"
        elif criterion.id in planned:
            label, style = "SCHEDULED", "white"
        else:
            label, style = "DEFERRED", "yellow"
        table.add_row(
            Text(label, style=style),
            Text(criterion.description),
            str(criterion.priority),
        )
    return table


def show_plan(console: Console, request: Request, plan: Plan) -> None:
    data = plan.public()
    console.print(
        Panel(
            Text(request.goal),
            title="PREFLIGHT / no model calls made",
            title_align="left",
            border_style="cyan",
        )
    )
    cost_low, cost_high = data["estimated_cost_usd_range"]
    time_low, time_high = data["estimated_seconds_range"]
    probability = plan.probability
    percent = probability.get("percent")
    cards = [
        _metric(
            "COST SCENARIO",
            f"{money(cost_low)} - {money(cost_high)}" if plan.feasible else "--",
            f"spending limit {money(request.budget.max_usd)}",
        ),
        _metric(
            "TIME SCENARIO",
            f"{duration(time_low)} - {duration(time_high)}" if plan.feasible else "--",
            f"deadline {duration(request.budget.max_seconds)}",
        ),
        _metric(
            "PLANNED COVERAGE",
            f"{plan.coverage:.0%}",
            "priority-weighted; not success",
            "green" if plan.coverage == 1 else "yellow",
        ),
        _metric(
            "SUCCESS PROBABILITY",
            "Not yet estimable" if percent is None else f"{percent:.1f}%",
            f"{probability.get('samples', 0)} labeled peers; not calibrated",
            "yellow",
        ),
    ]
    console.print(
        Columns(
            cards,
            equal=True,
            expand=True,
            width=35 if console.width >= 120 else 28,
        )
    )
    route = Table.grid(padding=(0, 2))
    route.add_column(style="dim")
    route.add_column(overflow="fold")
    route.add_row("Model", Text(plan.model.name if plan.model else "No feasible model"))
    route.add_row(
        "Selection", "Pinned by you" if request.model else "Automatic heuristic"
    )
    route.add_row("Strategy", plan.strategy.replace("_", " "))
    route.add_row("Endpoint", Text(plan.model.base_url if plan.model else "--"))
    route.add_row("Model calls", str(len(plan.calls)))
    console.print(
        Panel(
            route,
            title="EXECUTION ROUTE",
            title_align="left",
            border_style="bright_black",
        )
    )
    planned = {cid for call in plan.calls for cid in call.criteria}
    console.print(_criteria_table(request, planned))

    permissions = Table.grid(padding=(0, 2))
    permissions.add_column(style="dim")
    permissions.add_column(overflow="fold")
    permissions.add_row(
        "Read only", Text(", ".join(str(p) for p in request.permissions.read_roots))
    )
    permissions.add_row("Write artifacts", Text(str(request.permissions.output_root)))
    permissions.add_row(
        "Approved models", Text(", ".join(request.permissions.allowed_models))
    )
    permissions.add_row(
        "Send source excerpts",
        Text(
            "Approved in request"
            if request.permissions.send_documents
            else "Needs your approval",
            style="green" if request.permissions.send_documents else "yellow",
        ),
    )
    scope_policy = "allowed" if request.allow_scope_reduction else "forbidden"
    permissions.add_row(
        "Decisions",
        f"Scope reduction {scope_policy}; "
        f"on failure: {request.on_failure}; no shell, web browsing, or source writes",
    )
    if request.control_file:
        permissions.add_row("Live limits", Text(str(request.control_file)))
    console.print(
        Panel(
            permissions,
            title="PERMISSION ENVELOPE",
            title_align="left",
            border_style="bright_black",
        )
    )
    if plan.alternatives:
        table = Table(box=box.SIMPLE, expand=True, title="Other feasible routes")
        for column in ("MODEL", "STRATEGY", "COVERAGE", "COST UPPER", "TIME UPPER"):
            table.add_column(column, overflow="fold")
        for alternative in plan.alternatives[:4]:
            table.add_row(
                Text(alternative["model"]),
                alternative["strategy"].replace("_", " "),
                f"{alternative['planned_priority_coverage_percent']:.0f}%",
                money(alternative["cost_usd_upper_scenario"]),
                duration(alternative["seconds_upper_scenario"]),
            )
        console.print(table)
    notes = [
        Text(
            "Estimates are profile-based scenarios, not calibrated guarantees.",
            style="yellow",
        ),
        Text(
            "Evidence coverage checks exact quotes, not semantic correctness.",
            style="dim",
        ),
        Text(
            "Model selection is a heuristic, not a claim of global optimality.",
            style="dim",
        ),
    ]
    if not plan.feasible:
        notes.append(
            Text(
                "No candidate fits. Increase limits, adjust the profile, or defer.",
                style="yellow",
            )
        )
    console.print(Group(*notes))
    console.print()


class RunDashboard:
    def __init__(self, console: Console, request: Request):
        self.console = console
        self.request = request
        self.started = time.monotonic()
        self.completed = set()
        self.active = set()
        self.planned = set()
        self.cost = 0.0
        self.reservation = 0.0
        self.calls = 0
        self.model = "--"
        self.message = "Preparing approved documents"
        self.forecast = None
        self.live = None

    def __enter__(self):
        if self.console.is_terminal:
            self.live = Live(
                get_renderable=self.render,
                console=self.console,
                refresh_per_second=8,
                transient=True,
            )
            self.live.start()
        return self

    def __exit__(self, *exc):
        if self.live:
            self.live.stop()

    def event(self, event: dict) -> None:
        kind = event["type"]
        if kind in {"started", "replanned"}:
            self.forecast = event["plan"]
            self.planned = set(event["plan"]["planned_criteria"]) | self.completed
            self.message = "Checking the next step against remaining limits"
        elif kind == "call_reserved":
            self.active = set(event["criteria"])
            self.model = event["model"]
            self.calls += 1
            self.reservation = event["cost_usd_range"][1]
            self.cost += self.reservation
            self.message = "Reading source evidence with " + self.model
        elif kind == "call_returned":
            self.cost += event["accounted_usd"] - self.reservation
            self.reservation = 0
            self.message = "Checking quotes against supplied source excerpts"
        elif kind == "evidence_checked":
            self.completed.update(event["accepted_criteria"])
            self.active = set()
            self.message = "Evidence saved; replanning unfinished deliverables"
        elif kind == "call_failed":
            self.active = set()
            self.message = (
                "Call failed; retaining its reservation and deferring affected work"
            )
        elif kind == "finished":
            self.active = set()
            self.message = "Writing report and execution trace"
        if self.live:
            self.live.refresh()

    def render(self):
        total = sum(c.priority for c in self.request.criteria)
        done = sum(c.priority for c in self.request.criteria if c.id in self.completed)
        status = Table.grid(padding=(0, 3), expand=True)
        status.add_row(
            Text(f"{done / total:.0%} evidence coverage", style="bold cyan"),
            Text(duration(time.monotonic() - self.started)),
            Text(f"{money(self.cost)} accounted / reserved"),
            Text(f"{self.calls} model calls"),
        )
        forecast = Text("Estimating the next route", style="dim")
        if self.forecast:
            seconds = self.forecast["estimated_seconds_range"]
            cost = self.forecast["estimated_cost_usd_range"]
            scheduled = (
                sum(c.priority for c in self.request.criteria if c.id in self.planned)
                / total
            )
            forecast = Text(
                f"Current route: {scheduled:.0%} scheduled coverage"
                f" | remaining {duration(seconds[0])}-{duration(seconds[1])}"
                f" | {money(cost[0])}-{money(cost[1])}",
                style="dim",
            )
        return Panel(
            Group(
                Text(self.request.goal, style="bold"),
                Text(""),
                status,
                forecast,
                ProgressBar(total=total, completed=done, complete_style="cyan"),
                Text(""),
                _criteria_table(
                    self.request, self.planned, self.completed, self.active
                ),
                Text(self.message, style="dim"),
            ),
            title="SPAWN / EXECUTING",
            title_align="left",
            border_style="cyan",
        )


def show_result(console: Console, result: dict, request: Request) -> None:
    status = result["status"].replace("_", " ").upper()
    color = "green" if result["status"] == "evidence_ready" else "yellow"
    console.print(
        Panel(
            Group(
                Text(status, style=f"bold {color}"),
                Text(
                    f"{result['evidence_coverage_percent']:.0f}% evidence coverage"
                    f"  |  {money(result['accounted_cost_usd'])} accounted"
                    f"  |  {duration(result['elapsed_seconds'])}"
                ),
                Text(
                    "Task correctness has not been independently verified.", style="dim"
                ),
            ),
            title="RUN RECEIPT"
            if not result.get("simulation")
            else "OFFLINE DEMO RECEIPT",
            title_align="left",
            border_style=color,
        )
    )
    if result.get("simulation"):
        console.print(
            Text(
                "Synthetic responses/accounting. No API calls or benchmark results.",
                style="yellow",
            )
        )
    if "copilot" in result["cost_basis"]:
        console.print(
            Text(
                "Copilot usage-equivalent cost, not incremental invoice spending.",
                style="dim",
            )
        )
    completed = {f["criterion_id"] for f in result["findings"]}
    console.print(_criteria_table(request, completed, completed))
    if result["errors"]:
        console.print(
            Panel(
                Group(*(Text(e, style="yellow") for e in result["errors"])),
                title="Notices",
            )
        )
    directory = result["artifacts"]
    console.print(Text("Report  " + directory + "/report.md", style="cyan"))
    console.print(Text("Trace   " + directory + "/trace.jsonl", style="dim"))
    console.print(Text("Receipt " + directory + "/result.json", style="dim"))
    console.print()
