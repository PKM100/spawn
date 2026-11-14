from __future__ import annotations

import re
from pathlib import Path

from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from .contracts import Budget, Criterion, Model, Permissions, Request, positive
from .documents import TEXT_SUFFIXES
from .history import History
from .planner import Planner
from .profiles import copilot_profiles
from .providers import ProviderError
from .skills import install_skills


def offer_skills(console):
    home = Path.home()
    targets = (
        home / ".claude/skills/spawn/SKILL.md",
        home / ".agents/skills/spawn/SKILL.md",
    )
    if all(path.is_file() for path in targets):
        return
    console.print(
        Text(
            "Optional: bundled research skills can be installed in "
            "~/.claude/skills/spawn and ~/.agents/skills/spawn.\n"
            "They enable delegation, not spending or data-sharing authority.",
            style="dim",
        )
    )
    if Confirm.ask(
        "Install personal skills for Claude Code, Codex and Copilot?",
        default=False,
        console=console,
    ):
        try:
            install_skills("all", "user")
            console.print(
                Text("Skills installed. Reload your harness if needed.", style="green")
            )
        except (OSError, ValueError) as error:
            console.print(Text(f"Skills not installed: {error}", style="yellow"))


def _number(console, label, default=None, *, integer=False, zero=False):
    while True:
        value = Prompt.ask(
            label,
            default=str(default) if default is not None else None,
            console=console,
        )
        try:
            number = int(value) if integer else float(value)
            positive(number, label, zero=zero)
            return number
        except ValueError as error:
            console.print(Text(str(error), style="yellow"))


def _required(console, label):
    while True:
        value = Prompt.ask(label, console=console).strip()
        if value:
            return value
        console.print(
            Text("Please enter a value, or press Ctrl-C to cancel.", style="yellow")
        )


def choose_models(console) -> tuple[list[Model], str | None] | None:
    while True:
        provider = Prompt.ask(
            "Provider",
            choices=["copilot", "openai", "anthropic", "cancel"],
            default="copilot",
            console=console,
        )
        if provider == "cancel":
            return None
        if provider == "copilot":
            console.print(
                Text(
                    "Discovering signed-in Copilot models (network, no inference).",
                    style="dim",
                )
            )
            try:
                models = copilot_profiles()
            except (ProviderError, ValueError, TypeError, KeyError) as error:
                console.print(Text(str(error), style="yellow"))
                continue
            table = Table(title="Available models / catalog prices")
            table.add_column("Model ID", overflow="fold")
            table.add_column("Input / output USD per 1M", justify="right")
            for model in models:
                table.add_row(
                    Text(model.name),
                    f"{model.input_usd_per_million:g} / "
                    f"{model.output_usd_per_million:g}",
                )
            console.print(table)
            selected = Prompt.ask(
                "Model (auto permits selection among all listed models)",
                choices=["auto", *(m.name for m in models)],
                default="auto",
                console=console,
            )
            return (
                (models, None)
                if selected == "auto"
                else ([m for m in models if m.name == selected], selected)
            )
        model_id = _required(console, "Provider model ID")
        endpoint = Prompt.ask(
            "API base URL",
            default="https://api.openai.com/v1"
            if provider == "openai"
            else "https://api.anthropic.com/v1",
            console=console,
        )
        while True:
            key_env = Prompt.ask(
                "API key environment-variable name (- for no auth; never paste a key)",
                default="OPENAI_API_KEY"
                if provider == "openai"
                else "ANTHROPIC_API_KEY",
                console=console,
            )
            if key_env == "-" or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
                break
            console.print(
                Text("Enter a variable name, not a credential.", style="yellow")
            )
        console.print(
            Text(
                "Enter actual provider prices. Zero means confirmed free, not unknown.",
                style="dim",
            )
        )
        input_price = _number(console, "Input USD per million tokens", zero=True)
        output_price = _number(console, "Output USD per million tokens", zero=True)
        try:
            model = Model(
                model_id,
                provider,
                model_id,
                endpoint,
                input_price,
                output_price,
                output_tokens_per_second=100,
                input_tokens_per_second=2000,
                latency_seconds=3,
                api_key_env="" if key_env == "-" else key_env,
            )
        except ValueError as error:
            console.print(Text(str(error), style="yellow"))
            continue
        return [model], model.name


def collect_request(console) -> tuple[Request, Planner] | None:
    console.print(
        Panel(
            "No configuration files needed. Choose local documents and a finish line.\n"
            "Nothing is sent to a model until you review the preflight and approve.\n"
            "Press Ctrl-C at any time to cancel.",
            title="SPAWN / GUIDED RESEARCH",
            border_style="cyan",
        )
    )
    offer_skills(console)
    goal = _required(console, "What should the research accomplish?")
    console.print(
        Text(
            "Add UTF-8 source files, one path per line. No directory scanning.",
            style="dim",
        )
    )
    sources = []
    while True:
        value = Prompt.ask("Source file (blank when done)", default="", console=console)
        if not value.strip():
            if sources:
                break
            console.print(Text("Add at least one source file.", style="yellow"))
            continue
        try:
            path = Path(value.strip()).expanduser().resolve(strict=True)
            if not path.is_file():
                raise ValueError("Choose a file, not a directory")
            if path.suffix.lower() not in TEXT_SUFFIXES:
                raise ValueError("Use .txt, .md, .rst, .csv or .json source files")
            if path not in sources:
                sources.append(path)
        except (OSError, ValueError) as error:
            console.print(Text(str(error), style="yellow"))
    console.print(
        Text("List completion criteria in priority order, highest first.", style="dim")
    )
    descriptions = []
    while True:
        value = Prompt.ask(
            "Completion criterion (blank when done)", default="", console=console
        )
        if not value.strip():
            if descriptions:
                break
            console.print(Text("Add at least one criterion.", style="yellow"))
            continue
        descriptions.append(value.strip())
    while True:
        minimum = _number(
            console, "Minimum distinct sources per finding", 1, integer=True
        )
        if minimum <= len(sources):
            break
        console.print(
            Text("This exceeds the number of selected source files.", style="yellow")
        )
    selected = choose_models(console)
    if selected is None:
        return None
    models, pin = selected
    console.print(
        Text(
            "Time scenarios use uncalibrated throughput assumptions, not promises.",
            style="yellow",
        )
    )
    if any(m.provider == "copilot" for m in models):
        console.print(
            Text(
                "Copilot reserves $0.30/call. Usage equivalents; soft provider caps.",
                style="yellow",
            )
        )
    budget = Budget(
        _number(console, "Maximum USD (Copilot: usage equivalent)", 1, zero=True),
        _number(console, "Deadline in seconds", 120),
    )
    output = (
        Path(
            Prompt.ask(
                "Report and receipt directory",
                default=".spawn",
                console=console,
            )
        )
        .expanduser()
        .resolve()
    )
    reduce_scope = Confirm.ask(
        "Allow lower-priority work to be deferred if limits are tight?",
        default=True,
        console=console,
    )
    failure = Prompt.ask(
        "On failed findings",
        choices=["defer", "abort"],
        default="defer",
        console=console,
    )
    request = Request(
        goal,
        tuple(sources),
        tuple(
            Criterion(
                f"criterion-{index + 1}",
                description,
                priority=len(descriptions) - index,
                min_sources=minimum,
            )
            for index, description in enumerate(descriptions)
        ),
        budget,
        Permissions(
            tuple(dict.fromkeys(p.parent for p in sources)),
            output,
            tuple(m.name for m in models),
            False,
        ),
        model=pin,
        allow_scope_reduction=reduce_scope,
        on_failure=failure,
    )
    return request, Planner(models, History(output / "history.sqlite3"))
