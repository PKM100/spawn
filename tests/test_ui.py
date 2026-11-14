import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from spawn.cli import _approve, _interactive_request, main
from spawn.demo import demo_request
from spawn.documents import load_documents
from spawn.planner import Planner
from spawn.ui import THEME, RunDashboard, heading, show_plan


class UITests(unittest.TestCase):
    def test_preflight_is_readable_at_narrow_and_wide_terminal_widths(self):
        with tempfile.TemporaryDirectory() as directory:
            request, models = demo_request(Path(directory))
            plan = Planner(models).plan(request, load_documents(request))
            for width in (50, 80, 140):
                with self.subTest(width=width):
                    output = io.StringIO()
                    console = Console(
                        file=output,
                        width=width,
                        theme=THEME,
                        force_terminal=False,
                        color_system=None,
                    )
                    heading(console)
                    show_plan(console, request, plan)
                    text = output.getvalue()
                    self.assertIn("PREFLIGHT", text)
                    self.assertIn("Not yet estimable", text)
                    self.assertIn("PERMISSION ENVELOPE", text)
                    self.assertIn("demo-fast", text)
                    self.assertNotIn("\x1b", text)

    def test_live_events_update_evidence_and_reservation_accounting(self):
        with tempfile.TemporaryDirectory() as directory:
            request, _ = demo_request(Path(directory))
            dashboard = RunDashboard(Console(file=io.StringIO()), request)
            dashboard.event(
                {
                    "type": "call_reserved",
                    "model": "demo-fast",
                    "criteria": ["method"],
                    "cost_usd_range": [0.01, 0.03],
                }
            )
            self.assertEqual(dashboard.active, {"method"})
            self.assertEqual(dashboard.cost, 0.03)
            dashboard.event({"type": "call_returned", "accounted_usd": 0.02})
            dashboard.event(
                {"type": "evidence_checked", "accepted_criteria": ["method"]}
            )
            self.assertEqual(dashboard.completed, {"method"})
            self.assertAlmostEqual(dashboard.cost, 0.02)
            self.assertEqual(dashboard.active, set())

    def test_offline_demo_json_is_clean_and_never_uses_http(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with (
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError),
                patch("spawn.demo.time.sleep"),
                contextlib.redirect_stdout(output),
            ):
                code = main(["demo", "--json", "--output", directory])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result["simulation"])
            self.assertEqual(result["status"], "evidence_ready")
            self.assertEqual(result["evidence_coverage_percent"], 100)
            self.assertNotIn("\x1b", output.getvalue())

    def test_noninteractive_run_cannot_implicitly_approve_data_sharing(self):
        with tempfile.TemporaryDirectory() as directory:
            example = Path(directory) / "example"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", str(example), "--json"]), 0)
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                code = main(
                    [
                        "run",
                        str(example / "request.json"),
                        "--models",
                        str(example / "models.toml"),
                        "--yes",
                        "--json",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("send_documents=true", json.loads(output.getvalue())["error"])
            self.assertFalse((example / ".spawn").exists())

    def test_welcome_screen_needs_no_configuration(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([]), 0)
        self.assertIn("spawn demo", output.getvalue())

    def test_json_execution_preserves_interrupted_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            request, models = demo_request(Path(directory))
            output = io.StringIO()
            with (
                patch("spawn.cli._configure", return_value=(request, Planner(models))),
                patch("spawn.cli.Runtime") as runtime,
                contextlib.redirect_stdout(output),
            ):
                runtime.return_value.run.return_value = {
                    "stopping_reason": "interrupted"
                }
                code = main(
                    [
                        "run",
                        "request.json",
                        "--models",
                        "models.toml",
                        "--yes",
                        "--json",
                    ]
                )
            self.assertEqual(code, 130)
            self.assertEqual(
                json.loads(output.getvalue())["stopping_reason"], "interrupted"
            )

    def test_interactive_preflight_changes_deadline_and_pins_model(self):
        with tempfile.TemporaryDirectory() as directory:
            request, models = demo_request(Path(directory))
            console = Console(file=io.StringIO(), theme=THEME)
            with patch(
                "spawn.cli.Prompt.ask",
                side_effect=["t", "20", "m", "demo-fast", "r"],
            ):
                updated = _interactive_request(console, request, Planner(models))
            self.assertEqual(updated.budget.max_seconds, 20)
            self.assertEqual(updated.model, "demo-fast")
            plan = Planner(models).plan(updated, load_documents(updated))
            self.assertLess(plan.coverage, 1)

    def test_interactive_invalid_limit_can_be_corrected_without_exiting(self):
        with tempfile.TemporaryDirectory() as directory:
            request, models = demo_request(Path(directory))
            console = Console(file=io.StringIO(), theme=THEME)
            with patch(
                "spawn.cli.Prompt.ask",
                side_effect=["t", "not-a-number", "t", "30", "r"],
            ):
                updated = _interactive_request(console, request, Planner(models))
            self.assertEqual(updated.budget.max_seconds, 30)

    def test_declining_approval_returns_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            request, models = demo_request(Path(directory))
            console = Console(file=io.StringIO(), theme=THEME, force_terminal=True)
            with (
                patch("spawn.cli.sys.stdin.isatty", return_value=True),
                patch("spawn.cli.Confirm.ask", return_value=False),
            ):
                self.assertIsNone(
                    _approve(console, request, Planner(models), assume_yes=False)
                )


if __name__ == "__main__":
    unittest.main()
