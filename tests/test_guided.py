import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from rich.console import Console
from test_spawn import FakeProvider, model

from spawn.cli import main
from spawn.config import configuration_from_data
from spawn.profiles import copilot_profiles
from spawn.providers import ProviderError
from spawn.skills import install_skills
from spawn.wizard import choose_models, offer_skills


def catalog_entry(name, input_price=10, output_price=50, state="enabled"):
    return {
        "id": name,
        "policy": {"state": state},
        "billing": {
            "tokenPrices": {
                "batchSize": 1_000_000,
                "inputPrice": input_price,
                "outputPrice": output_price,
                "contextMax": 10000,
            }
        },
        "capabilities": {
            "limits": {
                "max_prompt_tokens": 32000,
                "max_context_window_tokens": 32000,
                "max_output_tokens": 8000,
            }
        },
        "supportedReasoningEfforts": ["none", "low"],
    }


class GuidedTests(unittest.TestCase):
    def setUp(self):
        offer = patch("spawn.wizard.offer_skills")
        offer.start()
        self.addCleanup(offer.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "approved note.txt"
        self.source.write_text("The method batches approved document reads.\n")
        self.output = self.root / "receipts"
        self.payload = {
            "models": [asdict(model())],
            "request": {
                "goal": "Summarize the approved note.",
                "sources": [str(self.source)],
                "criteria": [{"id": "method", "description": "Explain the method."}],
                "budget": {"max_usd": 1, "max_seconds": 120},
                "permissions": {
                    "read_roots": [str(self.root)],
                    "output_root": str(self.output),
                    "allowed_models": ["fast"],
                    "send_documents": False,
                },
            },
        }

    def invoke_stdin(self, arguments, payload=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        raw = json.dumps(self.payload if payload is None else payload)
        with (
            patch("spawn.cli.sys.stdin", io.StringIO(raw)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_stdin_preflight_needs_no_configs_or_paid_calls(self):
        with patch("spawn.cli.Runtime", side_effect=AssertionError):
            code, output, errors = self.invoke_stdin(["plan", "--stdin", "--json"])
        self.assertEqual(code, 0)
        self.assertFalse(errors)
        self.assertTrue(json.loads(output)["feasible"])
        self.assertFalse(self.output.exists())

    def test_stdin_execution_requires_and_honors_explicit_approval(self):
        provider = FakeProvider()
        with patch(
            "spawn.providers.HTTPProvider.complete", side_effect=provider.complete
        ):
            code, _, errors = self.invoke_stdin(["run", "--stdin", "--yes", "--json"])
            self.assertEqual(code, 2)
            self.assertIn("send_documents=true", json.loads(errors)["error"])
            self.assertEqual(provider.calls, [])
            self.payload["request"]["permissions"]["send_documents"] = True
            code, output, _ = self.invoke_stdin(["run", "--stdin", "--yes", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["status"], "evidence_ready")
        self.assertEqual(len(provider.calls), 1)
        self.assertFalse((self.root / "request.json").exists())
        self.assertFalse((self.root / "models.toml").exists())

    def test_stdin_cannot_expand_its_contract_with_command_line_overrides(self):
        code, _, errors = self.invoke_stdin(
            ["plan", "--stdin", "--json", "--usd", "10"]
        )
        self.assertEqual(code, 2)
        self.assertIn("contract overrides", json.loads(errors)["error"])

    def test_stdin_discovery_does_not_broaden_allowed_models(self):
        self.payload.pop("models")
        self.payload["copilot_models"] = ["fast"]
        self.payload["request"]["permissions"]["allowed_models"] = ["other"]
        with patch("spawn.profiles.copilot_profiles", return_value=[model()]):
            code, _, errors = self.invoke_stdin(["plan", "--stdin", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("No approved model", json.loads(errors)["error"])
        self.assertFalse(self.output.exists())

    def test_invalid_envelopes_do_not_discover_models(self):
        with patch("spawn.profiles.copilot_profiles", side_effect=AssertionError):
            for data in (
                {"request": self.payload["request"], "copilot_models": None},
                {**self.payload, "copilot_models": ["fast"]},
                {**self.payload, "execute_shell": True},
            ):
                with self.subTest(data=list(data)), self.assertRaises(ValueError):
                    configuration_from_data(data, self.root)

    def test_nested_run_is_rejected_before_a_provider_call(self):
        self.payload["request"]["permissions"]["send_documents"] = True
        with (
            patch.dict("os.environ", {"SPAWN_ACTIVE": "1"}),
            patch("spawn.cli.Runtime", side_effect=AssertionError),
        ):
            code, _, errors = self.invoke_stdin(["run", "--stdin", "--yes", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("Nested", json.loads(errors)["error"])

    def test_bare_spawn_collects_details_and_runs_only_after_approval(self):
        provider = FakeProvider()
        output = io.StringIO()
        console = Console(file=output, force_terminal=True, color_system=None)
        prompts = [
            "Summarize the note.",
            str(self.source),
            "",
            "Explain the method.",
            "Identify limitations.",
            "",
            "1",
            "1",
            "120",
            str(self.output),
            "defer",
            "r",
        ]
        with (
            patch("spawn.cli.make_console", return_value=console),
            patch("spawn.cli.sys.stdin.isatty", return_value=True),
            patch("spawn.wizard.choose_models", return_value=([model()], "fast")),
            patch("spawn.cli.Prompt.ask", side_effect=prompts),
            patch("spawn.cli.Confirm.ask", side_effect=[True, True]),
            patch(
                "spawn.providers.HTTPProvider.complete", side_effect=provider.complete
            ),
        ):
            self.assertEqual(main([]), 0)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0][1].criteria, ("criterion-1", "criterion-2"))
        self.assertIn("GUIDED RESEARCH", output.getvalue())
        self.assertIn("EVIDENCE READY", output.getvalue())
        self.assertFalse((self.root / "models.toml").exists())

    def test_declining_guided_run_never_writes_or_calls(self):
        console = Console(file=io.StringIO(), force_terminal=True)
        with (
            patch("spawn.cli.make_console", return_value=console),
            patch("spawn.cli.sys.stdin.isatty", return_value=True),
            patch("spawn.wizard.choose_models", return_value=([model()], "fast")),
            patch(
                "spawn.cli.Prompt.ask",
                side_effect=[
                    "Goal",
                    str(self.source),
                    "",
                    "Criterion",
                    "",
                    "1",
                    "1",
                    "120",
                    str(self.output),
                    "defer",
                    "r",
                ],
            ),
            patch("spawn.cli.Confirm.ask", side_effect=[True, False]),
            patch("spawn.cli.Runtime", side_effect=AssertionError),
        ):
            self.assertEqual(main(["start"]), 0)
        self.assertFalse(self.output.exists())

    def test_headless_start_does_not_prompt_or_discover(self):
        with (
            patch("spawn.cli.sys.stdin.isatty", return_value=False),
            patch("spawn.cli.collect_request", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(["start"]), 2)

    def test_guided_eof_cancels_cleanly(self):
        with (
            patch(
                "spawn.cli.make_console",
                return_value=Console(file=io.StringIO(), force_terminal=True),
            ),
            patch("spawn.cli.sys.stdin.isatty", return_value=True),
            patch("spawn.cli.Prompt.ask", side_effect=EOFError),
        ):
            self.assertEqual(main([]), 130)


class ProfileAndSkillTests(unittest.TestCase):
    def test_first_launch_skill_offer_is_opt_in_and_not_repeated_after_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            console = Console(file=io.StringIO())
            with (
                patch("spawn.wizard.Path.home", return_value=root),
                patch("spawn.wizard.Confirm.ask", return_value=False),
            ):
                offer_skills(console)
            self.assertFalse((root / ".agents").exists())
            with (
                patch("spawn.wizard.Path.home", return_value=root),
                patch("spawn.wizard.Confirm.ask", return_value=True),
            ):
                offer_skills(console)
            self.assertTrue((root / ".agents/skills/spawn/SKILL.md").is_file())
            self.assertTrue((root / ".claude/skills/spawn/SKILL.md").is_file())
            with (
                patch("spawn.wizard.Path.home", return_value=root),
                patch("spawn.wizard.Confirm.ask", side_effect=AssertionError),
            ):
                offer_skills(console)

    def test_catalog_prices_convert_credits_and_keep_short_context_boundary(self):
        entries = [
            catalog_entry("expensive", 100, 500),
            catalog_entry("cheap"),
            catalog_entry("disabled", state="disabled"),
        ]
        with patch("spawn.copilot_provider.list_models", return_value=entries):
            profiles = copilot_profiles()
            pinned = copilot_profiles(["expensive"])
        self.assertEqual([m.name for m in profiles], ["cheap", "expensive"])
        self.assertAlmostEqual(profiles[0].input_usd_per_million, 0.10)
        self.assertAlmostEqual(profiles[0].output_usd_per_million, 0.50)
        self.assertEqual(profiles[0].context_tokens, 10000)
        self.assertEqual([m.name for m in pinned], ["expensive"])

    def test_unknown_models_and_invalid_lists_never_silently_fallback(self):
        with patch(
            "spawn.copilot_provider.list_models", return_value=[catalog_entry("a")]
        ):
            with self.assertRaisesRegex(ValueError, "Unavailable"):
                copilot_profiles(["unknown"])
        with patch("spawn.copilot_provider.list_models", side_effect=AssertionError):
            for ids in ([], "auto", [None]):
                with self.subTest(ids=ids), self.assertRaises(ValueError):
                    copilot_profiles(ids)

    def test_discovery_failure_can_be_cancelled_and_manual_models_need_no_config(self):
        console = Console(file=io.StringIO())
        with (
            patch(
                "spawn.wizard.copilot_profiles", side_effect=ProviderError("Sign in")
            ),
            patch("spawn.wizard.Prompt.ask", side_effect=["copilot", "cancel"]),
        ):
            self.assertIsNone(choose_models(console))
        with patch(
            "spawn.wizard.Prompt.ask",
            side_effect=[
                "openai",
                "user-model",
                "http://127.0.0.1:8080/v1",
                "-",
                "0",
                "0",
            ],
        ):
            models, pin = choose_models(console)
        self.assertEqual(pin, "user-model")
        self.assertEqual(models[0].api_key_env, "")

    def test_all_agents_share_compatible_skill_files_and_install_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = install_skills("all", "project", root)
            self.assertEqual(len(first["installed"]), 2)
            self.assertEqual(
                install_skills("copilot", "project", root)["installed"], []
            )
            claude = root / ".claude/skills/spawn/SKILL.md"
            shared = root / ".agents/skills/spawn/SKILL.md"
            self.assertEqual(claude.read_bytes(), shared.read_bytes())

    def test_install_never_overwrites_custom_skills_or_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install_skills("codex", "project", root)
            custom = root / ".agents/skills/spawn/SKILL.md"
            custom.write_text("User-maintained skill")
            with self.assertRaises(FileExistsError):
                install_skills("all", "project", root)
            self.assertFalse((root / ".claude").exists())
            with tempfile.TemporaryDirectory() as elsewhere:
                (root / ".claude").symlink_to(elsewhere, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink"):
                    install_skills("claude", "project", root)
