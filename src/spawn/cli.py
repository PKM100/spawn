import argparse
import json
import sys
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

from .config import load_models, load_request
from .contracts import Budget, Request
from .demo import DemoProvider, demo_request
from .documents import load_documents
from .evaluation import evaluate
from .history import History
from .planner import Planner
from .runtime import Runtime, read_control
from .ui import RunDashboard, heading, make_console, show_plan, show_result


def _arguments(argv):
    parser = argparse.ArgumentParser(
        prog="spawn",
        description="Preflight, approve, and execute constrained document research.",
    )
    commands = parser.add_subparsers(dest="command")
    init = commands.add_parser("init", help="Create an editable example configuration")
    init.add_argument("directory", type=Path)
    init.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    for name in ("plan", "run", "launch"):
        command = commands.add_parser(
            name,
            help={
                "plan": "Preview cost, time, coverage, models, and permissions",
                "run": "Approve and execute a research request",
                "launch": "Interactively adjust constraints before approving execution",
            }[name],
        )
        command.add_argument("request", type=Path)
        command.add_argument("--models", type=Path, required=True)
        command.add_argument("--model", help="Pin an approved profile by name")
        command.add_argument(
            "--seconds", type=float, help="Override the initial deadline"
        )
        command.add_argument(
            "--usd", type=float, help="Override the initial spending limit"
        )
        if name != "launch":
            command.add_argument(
                "--json", action="store_true", help="Emit machine-readable JSON"
            )
        if name == "run":
            command.add_argument(
                "--yes",
                action="store_true",
                help="Use permissions already approved in the request",
            )
    demo = commands.add_parser(
        "demo", help="Try the terminal UI offline; no API charges"
    )
    demo.add_argument("--output", type=Path, default=Path(".spawn/demo"))
    demo.add_argument("--seconds", type=float, default=120)
    demo.add_argument("--json", action="store_true")
    label = commands.add_parser("label", help="Record a manually reviewed task outcome")
    label.add_argument("run_id")
    label.add_argument("--history", type=Path, required=True)
    label.add_argument("--json", action="store_true")
    outcome = label.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--success", action="store_true")
    outcome.add_argument("--failure", action="store_true")
    score = commands.add_parser(
        "evaluate", help="Score held-out JSONL forecasts/outcomes"
    )
    score.add_argument("records", type=Path)
    score.add_argument("--json", action="store_true")
    return parser, parser.parse_args(argv)


def _configure(args):
    request = load_request(args.request)
    request = replace(
        request,
        model=args.model if args.model is not None else request.model,
        budget=Budget(
            args.usd if args.usd is not None else request.budget.max_usd,
            args.seconds if args.seconds is not None else request.budget.max_seconds,
        ),
    )
    request = replace(request, budget=read_control(request, request.budget))
    planner = Planner(
        load_models(args.models),
        History(request.permissions.output_root / "history.sqlite3"),
    )
    return request, planner


def _interactive_request(console, request: Request, planner: Planner):
    documents = load_documents(request)
    while True:
        plan = planner.plan(request, documents)
        show_plan(console, request, plan)
        action = Prompt.ask(
            "[cyan]r[/] run   [cyan]m[/] model   [cyan]t[/] time   "
            "[cyan]b[/] budget   [dim]q quit[/]",
            choices=["r", "m", "t", "b", "q"],
            default="r" if plan.feasible else "t",
            console=console,
        )
        if action == "q":
            return None
        if action == "r":
            if not plan.feasible:
                console.print(
                    Text(
                        "No feasible route. Adjust the constraints first.",
                        style="yellow",
                    )
                )
                continue
            return request
        if action == "m":
            aliases = [
                m.name
                for m in planner.models
                if m.name in request.permissions.allowed_models
            ]
            selected = Prompt.ask(
                "Choose a model (auto lets Spawn select)",
                choices=["auto", *aliases],
                default=request.model or "auto",
                console=console,
            )
            request = replace(request, model=None if selected == "auto" else selected)
        else:
            is_time = action == "t"
            try:
                value = float(
                    Prompt.ask(
                        "Deadline in seconds" if is_time else "Spending limit in USD",
                        default=str(
                            request.budget.max_seconds
                            if is_time
                            else request.budget.max_usd
                        ),
                        console=console,
                    )
                )
                limits = Budget(
                    request.budget.max_usd if is_time else value,
                    value if is_time else request.budget.max_seconds,
                )
                request = replace(request, budget=read_control(request, limits))
            except ValueError as error:
                console.print(Text(f"Invalid limit: {error}", style="yellow"))


def _approve(console, request, planner, *, assume_yes):
    if assume_yes:
        if not request.permissions.send_documents:
            raise PermissionError(
                "--yes requires send_documents=true in the request; "
                "otherwise use interactive approval"
            )
        return request
    if not console.is_terminal or not sys.stdin.isatty():
        raise ValueError(
            "Noninteractive execution requires --yes and explicitly approved "
            "send_documents=true"
        )
    endpoints = [
        f"{m.name}: {m.base_url} ({m.model_id})"
        for m in planner.models
        if m.name in request.permissions.allowed_models
        and (request.model is None or m.name == request.model)
    ]
    console.print(
        Panel(
            Text("\n".join(endpoints)),
            title="Approved destinations for source excerpts",
            border_style="yellow",
        )
    )
    if not Confirm.ask(
        "Send excerpts to these destinations and write the displayed artifacts?",
        default=False,
        console=console,
    ):
        return None
    return replace(
        request, permissions=replace(request.permissions, send_documents=True)
    )


def main(argv: list[str] | None = None) -> int:
    _, args = _arguments(argv)
    console = make_console()
    machine = getattr(args, "json", False)
    try:
        if args.command is None:
            heading(console)
            console.print(
                Panel(
                    "[bold]Your goal. Your limits. An execution contract.[/]\n\n"
                    "[cyan]spawn demo[/]                   Try the offline experience\n"
                    "[cyan]spawn init my-research[/]         Create example inputs\n"
                    "[cyan]spawn launch request.json --models models.toml[/]\n"
                    "                               Adjust, approve, and execute\n\n"
                    "[dim]Use --json for scripts. Use --help for all commands.[/]",
                    border_style="cyan",
                    title="START HERE",
                    title_align="left",
                )
            )
            return 0
        if not machine:
            heading(
                console,
                "Offline experience / no network"
                if args.command == "demo"
                else "Know the tradeoff. Then spawn.",
            )
        if args.command == "init":
            if args.directory.exists():
                raise ValueError(
                    "Choose a new directory; init never overwrites existing files"
                )
            args.directory.mkdir(parents=True)
            for name in ("models.toml", "request.json", "source.txt", "control.json"):
                template = (
                    files("spawn.templates").joinpath(name).read_text(encoding="utf-8")
                )
                (args.directory / name).write_text(template, encoding="utf-8")
            result = {
                "created": str(args.directory.resolve()),
                "next": (
                    "Edit model IDs, prices, throughput assumptions, and permissions; "
                    "then run spawn plan"
                ),
            }
        elif args.command == "label":
            History(args.history).label(args.run_id, args.success)
            result = {"run_id": args.run_id, "success": args.success}
        elif args.command == "evaluate":
            rows = [
                json.loads(line)
                for line in args.records.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            result = evaluate(rows)
        else:
            if args.command == "demo":
                request, models = demo_request(args.output, args.seconds)
                planner = Planner(models)
                provider = DemoProvider()
            else:
                request, planner = _configure(args)
                provider = None
            if args.command == "launch":
                if not console.is_terminal or not sys.stdin.isatty():
                    raise ValueError(
                        "launch needs a terminal; use plan/run --json for automation"
                    )
                request = _interactive_request(console, request, planner)
                if request is None:
                    console.print(Text("Nothing started.", style="dim"))
                    return 0
            plan = planner.plan(request, load_documents(request))
            if args.command == "plan":
                if machine:
                    print(json.dumps(plan.public(), indent=2))
                else:
                    show_plan(console, request, plan)
                return 0
            if not machine and args.command != "launch":
                show_plan(console, request, plan)
            if args.command != "demo":
                request = _approve(
                    console,
                    request,
                    planner,
                    assume_yes=getattr(args, "yes", False),
                )
                if request is None:
                    console.print(Text("Nothing started.", style="dim"))
                    return 0
            if machine:
                result = Runtime(planner, provider).run(request)
            else:
                with RunDashboard(console, request) as dashboard:
                    result = Runtime(planner, provider, on_event=dashboard.event).run(
                        request
                    )
                show_result(console, result, request)
                return 130 if result["stopping_reason"] == "interrupted" else 0
        if machine:
            print(json.dumps(result, indent=2))
        else:
            console.print_json(data=result)
        return 130 if result.get("stopping_reason") == "interrupted" else 0
    except KeyboardInterrupt:
        if not machine:
            console.print(
                Text("\nCancelled. No further work will be started.", style="yellow")
            )
        return 130
    except (OSError, ValueError, KeyError, TypeError) as error:
        if machine:
            print(json.dumps({"error": str(error)}), file=sys.stderr)
        else:
            console.print(
                Panel(
                    Text(str(error)),
                    title="SPAWN / ACTION REQUIRED",
                    border_style="red",
                )
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
