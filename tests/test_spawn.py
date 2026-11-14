import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from spawn.cli import main
from spawn.config import load_request
from spawn.contracts import Budget, Criterion, Model, Permissions, Request
from spawn.documents import load_documents
from spawn.evaluation import evaluate
from spawn.evidence import validate_findings
from spawn.history import MIN_OUTCOMES, History, cohort_key
from spawn.planner import Planner
from spawn.providers import HTTPProvider, ProviderError, Response
from spawn.runtime import Runtime, read_control


def model(**changes):
    profile = Model(
        name="fast",
        provider="openai",
        model_id="test-model",
        base_url="https://example.invalid/v1",
        input_usd_per_million=1,
        output_usd_per_million=4,
        input_tokens_per_second=2000,
        output_tokens_per_second=100,
        latency_seconds=1,
        context_tokens=64000,
        max_output_tokens=4000,
    )
    return replace(profile, **changes)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeProvider:
    def __init__(self, *, hook=None, response=None):
        self.calls = []
        self.hook = hook
        self.response = response

    def complete(self, profile, call, timeout):
        self.calls.append((profile.name, call, timeout))
        if self.hook:
            self.hook()
        if isinstance(self.response, Exception):
            raise self.response
        if self.response is not None:
            return self.response
        data = json.loads(call.prompt)
        evidence = [
            {"source_id": d["source_id"], "quote": d["text"].splitlines()[0]}
            for d in data["documents"]
        ]
        return Response(
            json.dumps(
                {
                    "findings": [
                        {
                            "criterion_id": c["id"],
                            "answer": "A test finding.",
                            "evidence": evidence,
                        }
                        for c in data["criteria"]
                    ]
                }
            ),
            300,
            100,
        )


class SpawnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.txt"
        self.source.write_text(
            "The method batches document reads.\nEvaluation uses held-out tasks.",
            encoding="utf-8",
        )
        self.request = Request(
            "Explain the method and evaluation.",
            (self.source,),
            (
                Criterion("method", "Explain the method", priority=3),
                Criterion("evaluation", "Describe the evaluation"),
            ),
            Budget(1, 120),
            Permissions((self.root,), self.root / "runs", ("fast", "slow"), True),
        )
        self.models = [model(), model(name="slow", output_tokens_per_second=10)]
        self.documents = load_documents(self.request)

    def test_preflight_has_no_network_or_output_writes(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError):
            plan = Planner(self.models).plan(self.request, self.documents)
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.model.name, "fast")
        self.assertEqual(plan.coverage, 1)
        self.assertIsNone(plan.probability["percent"])
        self.assertFalse((self.root / "runs").exists())

    def test_copilot_uses_reported_billing_instead_of_token_prices(self):
        profile = model(provider="copilot", per_call_usd_reserve=0.30)
        provider = FakeProvider(
            response=Response(
                '{"findings":[]}',
                300,
                100,
                reported_cost_usd=0.002,
                billing={"basis": "copilot_ai_credits_equivalent"},
            )
        )
        result = Runtime(Planner([profile]), provider).run(self.request)
        self.assertAlmostEqual(result["accounted_cost_usd"], 0.002)
        self.assertEqual(result["cost_basis"], "copilot_ai_credits_equivalent")

    def test_unknown_copilot_billing_retains_reservation_even_with_tokens(self):
        profile = model(provider="copilot", per_call_usd_reserve=0.30)
        result = Runtime(
            Planner([profile]),
            FakeProvider(response=Response('{"findings":[]}', 300, 100)),
        ).run(self.request)
        self.assertAlmostEqual(result["accounted_cost_usd"], 0.30)
        self.assertEqual(result["cost_basis"], "includes_reservations")

    def test_pin_model_is_honored(self):
        request = replace(self.request, model="slow", budget=Budget(1, 1000))
        plan = Planner(self.models).plan(request, self.documents)
        self.assertEqual(plan.model.name, "slow")

    def test_unapproved_or_unknown_model_is_rejected(self):
        for name in ("missing", "unapproved"):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                Planner(self.models).plan(
                    replace(self.request, model=name), self.documents
                )

    def test_shorter_deadline_reduces_scope_and_defers_lower_priority(self):
        planner = Planner([model()])
        full = planner.plan(self.request, self.documents)
        single = planner.plan(
            replace(self.request, criteria=(self.request.criteria[0],)), self.documents
        )
        seconds = sum(c.seconds_high for c in single.calls) + 0.01
        partial = planner.plan(
            replace(self.request, budget=Budget(1, seconds)), self.documents
        )
        self.assertLess(seconds, sum(c.seconds_high for c in full.calls))
        self.assertEqual(partial.deferred, ["evaluation"])
        self.assertEqual(partial.coverage, 0.75)
        self.assertIsNone(partial.probability["percent"])

    def test_scope_reduction_can_be_forbidden(self):
        request = replace(
            self.request, budget=Budget(1, 20), allow_scope_reduction=False
        )
        self.assertFalse(Planner([model()]).plan(request, self.documents).feasible)

    def test_impossible_budget_does_not_invoke_provider(self):
        provider = FakeProvider()
        result = Runtime(Planner(self.models), provider).run(
            replace(self.request, budget=Budget(0, 120))
        )
        self.assertEqual(provider.calls, [])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["deferred"], ["method", "evaluation"])

    def test_free_local_model_can_run_with_zero_dollar_budget(self):
        free = model(input_usd_per_million=0, output_usd_per_million=0)
        result = Runtime(Planner([free]), FakeProvider()).run(
            replace(self.request, budget=Budget(0, 120))
        )
        self.assertEqual(result["status"], "evidence_ready")
        self.assertEqual(result["accounted_cost_usd"], 0)

    def test_context_limit_is_respected(self):
        plan = Planner([model(context_tokens=100)]).plan(self.request, self.documents)
        self.assertFalse(plan.feasible)

    def test_small_output_limit_selects_incremental_strategy(self):
        plan = Planner([model(max_output_tokens=750)]).plan(
            self.request, self.documents
        )
        self.assertEqual(plan.strategy, "incremental")
        self.assertEqual(len(plan.calls), 2)

    def test_multi_source_requirement_cannot_be_faked(self):
        request = replace(
            self.request,
            criteria=(Criterion("compare", "Compare two sources", min_sources=2),),
        )
        self.assertFalse(Planner(self.models).plan(request, self.documents).feasible)

    def test_impossible_criterion_does_not_block_independent_feasible_work(self):
        request = replace(
            self.request,
            criteria=(
                Criterion("compare", "Compare two sources", priority=3, min_sources=2),
                Criterion("method", "Explain the method"),
            ),
        )
        plan = Planner(self.models).plan(request, self.documents)
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.deferred, ["compare"])
        self.assertEqual(plan.calls[0].criteria, ("method",))

    def test_interruption_preserves_a_receipt_without_claiming_unbilled_work(self):
        provider = FakeProvider()
        with patch.object(provider, "complete", side_effect=KeyboardInterrupt):
            result = Runtime(Planner(self.models), provider).run(self.request)
        self.assertEqual(result["stopping_reason"], "interrupted")
        self.assertEqual(result["cost_basis"], "includes_reservations")
        self.assertTrue((Path(result["artifacts"]) / "report.md").exists())

    def test_symlink_cannot_escape_read_permission(self):
        inside = self.root / "allowed"
        inside.mkdir()
        link = inside / "source.txt"
        link.symlink_to(self.source)
        request = replace(
            self.request,
            sources=(link,),
            permissions=replace(self.request.permissions, read_roots=(inside,)),
        )
        with self.assertRaises(PermissionError):
            load_documents(request)

    def test_duplicate_sources_are_not_multiple_independent_sources(self):
        request = replace(self.request, sources=(self.source, self.source))
        self.assertEqual(len(load_documents(request)), 1)

    def test_external_processing_requires_upfront_permission(self):
        provider = FakeProvider()
        request = replace(
            self.request,
            permissions=replace(self.request.permissions, send_documents=False),
        )
        with self.assertRaises(PermissionError):
            Runtime(Planner(self.models), provider).run(request)
        self.assertEqual(provider.calls, [])
        self.assertFalse(request.permissions.output_root.exists())

    def test_run_writes_cited_report_trace_and_preflight(self):
        result = Runtime(Planner(self.models), FakeProvider()).run(self.request)
        self.assertEqual(result["status"], "evidence_ready")
        self.assertEqual(result["evidence_coverage_percent"], 100)
        self.assertFalse(result["success_verified"])
        self.assertAlmostEqual(result["accounted_cost_usd"], 0.0007)
        directory = Path(result["artifacts"])
        report = (directory / "report.md").read_text()
        self.assertIn("[S1]", report)
        self.assertIn("The method batches document reads.", report)
        self.assertTrue((directory / "preflight.json").exists())
        events = [
            json.loads(line)
            for line in (directory / "trace.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-1]["type"], "finished")
        self.assertNotIn("prompt", (directory / "trace.jsonl").read_text())

    def test_invented_quotes_are_rejected(self):
        response = Response(
            json.dumps(
                {
                    "findings": [
                        {
                            "criterion_id": "method",
                            "answer": "Fabricated answer",
                            "evidence": [
                                {"source_id": "S1", "quote": "Invented quote"}
                            ],
                        }
                    ]
                }
            ),
            300,
            100,
        )
        result = Runtime(Planner(self.models), FakeProvider(response=response)).run(
            self.request
        )
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("exact-quote" in e for e in result["errors"]))

    def test_quote_must_be_in_supplied_excerpt_not_just_document(self):
        call = Planner(self.models).plan(self.request, self.documents).calls[0]
        call = replace(call, excerpts={"S1": "The method batches document reads."})
        text = json.dumps(
            {
                "findings": [
                    {
                        "criterion_id": "method",
                        "answer": "A claim",
                        "evidence": [
                            {
                                "source_id": "S1",
                                "quote": "Evaluation uses held-out tasks.",
                            }
                        ],
                    }
                ]
            }
        )
        accepted, errors = validate_findings(
            text, call, self.request.criteria, self.documents
        )
        self.assertEqual(accepted, [])
        self.assertTrue(errors)

    def test_malformed_output_is_deferred_without_retry(self):
        provider = FakeProvider(response=Response("not JSON", 300, 100))
        result = Runtime(Planner(self.models), provider).run(self.request)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result["status"], "blocked")

    def test_timeout_keeps_reservation_in_accounting(self):
        provider = FakeProvider(response=ProviderError("timeout"))
        result = Runtime(Planner(self.models), provider).run(self.request)
        self.assertEqual(len(provider.calls), 1)
        self.assertGreater(result["accounted_cost_usd"], 0)
        self.assertEqual(result["cost_basis"], "includes_reservations")

    def test_missing_usage_is_not_treated_as_free(self):
        response = FakeProvider().complete(
            model(),
            Planner(self.models).plan(self.request, self.documents).calls[0],
            10,
        )
        provider = FakeProvider(response=replace(response, input_tokens=None))
        result = Runtime(Planner(self.models), provider).run(self.request)
        self.assertGreater(result["accounted_cost_usd"], 0)
        self.assertEqual(result["cost_basis"], "includes_reservations")

    def test_reservation_violation_is_reported_and_stops_execution(self):
        response = Response('{"findings":[]}', 10_000_000, 100)
        provider = FakeProvider(response=response)
        result = Runtime(Planner(self.models), provider).run(self.request)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result["stopping_reason"], "reservation_exceeded")
        self.assertFalse(result["within_limits"])

    def test_live_shortening_preserves_partial_work(self):
        control = self.root / "control.json"
        control.write_text('{"max_usd":1,"max_seconds":120}')
        clock = Clock()

        def shorten():
            clock.now += 3
            control.write_text('{"max_usd":1,"max_seconds":2}')

        provider = FakeProvider(hook=shorten)
        request = replace(self.request, control_file=control)
        result = Runtime(
            Planner([model(max_output_tokens=750)]), provider, clock=clock
        ).run(request)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["deferred"], ["evaluation"])

    def test_deadline_update_during_last_call_is_still_accounted(self):
        control = self.root / "control.json"
        control.write_text('{"max_usd":1,"max_seconds":120}')
        clock = Clock()

        def shorten():
            clock.now += 3
            control.write_text('{"max_usd":1,"max_seconds":2}')

        result = Runtime(
            Planner(self.models), FakeProvider(hook=shorten), clock=clock
        ).run(replace(self.request, control_file=control))
        self.assertEqual(result["final_limits"]["max_seconds"], 2)
        self.assertFalse(result["within_limits"])

    def test_live_control_cannot_expand_permissions_or_budget(self):
        control = self.root / "control.json"
        request = replace(self.request, control_file=control)
        for content in (
            '{"max_usd":2}',
            '{"max_seconds":200}',
        ):
            control.write_text(content)
            with self.subTest(content=content):
                self.assertEqual(read_control(request, request.budget), request.budget)
        control.write_text('{"allowed_models":["other"]}')
        with self.assertRaises(ValueError):
            read_control(request, request.budget)

    def test_unknown_credential_fails_without_exposing_secret(self):
        profile = model(api_key_env="SPAWN_TEST_MISSING_KEY")
        call = Planner([profile]).plan(self.request, self.documents).calls[0]
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ProviderError):
            HTTPProvider().complete(profile, call, 1)

    def test_provider_decoders_include_cached_input_and_reasoning_usage(self):
        anthropic = HTTPProvider.decode(
            "anthropic",
            {
                "content": [{"type": "text", "text": "answer"}],
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 30,
                    "output_tokens": 40,
                },
            },
        )
        self.assertEqual(anthropic.input_tokens, 60)
        openai = HTTPProvider.decode(
            "openai",
            {
                "choices": [
                    {"message": {"content": "answer"}, "finish_reason": "length"}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 200},
            },
        )
        self.assertEqual(openai.output_tokens, 200)
        self.assertTrue(openai.truncated)

    def test_unlabeled_or_small_history_never_fabricates_probability(self):
        history = History(self.root / "history.sqlite3")
        for index in range(MIN_OUTCOMES - 1):
            history.record(str(index), "cohort", 0.01, 1, eligible=True)
            history.label(str(index), True)
        forecast = history.forecast("cohort")
        self.assertIsNone(forecast["percent"])
        self.assertEqual(forecast["samples"], MIN_OUTCOMES - 1)

    def test_historical_rate_is_explicitly_not_calibrated(self):
        history = History(self.root / "history.sqlite3")
        for index in range(MIN_OUTCOMES):
            history.record(str(index), "cohort", 0.01, 1, eligible=True)
            history.label(str(index), index < 15)
        forecast = history.forecast("cohort")
        self.assertEqual(forecast["percent"], 75)
        self.assertFalse(forecast["calibrated"])
        self.assertLess(forecast["interval_percent"][0], 75)
        self.assertGreater(forecast["interval_percent"][1], 75)

    def test_over_limit_or_partial_run_cannot_be_labeled_successful(self):
        history = History(self.root / "history.sqlite3")
        history.record("partial", "cohort", 0.01, 1, eligible=False)
        with self.assertRaises(ValueError):
            history.label("partial", True)
        history.label("partial", False)

    def test_history_does_not_pool_different_models_or_budget_scales(self):
        key = cohort_key(self.request, model(), "single_pass", 100)
        other_model = cohort_key(
            self.request, model(model_id="other"), "single_pass", 100
        )
        other_budget = cohort_key(
            replace(self.request, budget=Budget(0.001, 5)), model(), "single_pass", 100
        )
        self.assertNotEqual(key, other_model)
        self.assertNotEqual(key, other_budget)

    def test_evaluation_scores_held_out_predictions(self):
        row = {
            "run_id": "one",
            "split": "test",
            "prediction": {
                "cost_usd": [0.1, 0.3],
                "seconds": [1, 3],
                "success_probability": 0.8,
            },
            "outcome": {"cost_usd": 0.2, "seconds": 2, "success": True},
        }
        metrics = evaluate([row])
        self.assertAlmostEqual(metrics["brier_score"], 0.04)
        self.assertEqual(metrics["observed_interval_coverage"]["cost_usd"], 1)
        with self.assertRaises(ValueError):
            evaluate([{**row, "split": "train"}])
        with self.assertRaises(ValueError):
            evaluate([row, row])

    def test_cli_init_plan_and_deadline_override(self):
        directory = self.root / "example"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["init", str(directory), "--json"]), 0)
        request = load_request(directory / "request.json")
        self.assertFalse(request.permissions.send_documents)
        for seconds, feasible in (("120", True), ("0.01", False)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "plan",
                        str(directory / "request.json"),
                        "--models",
                        str(directory / "models.toml"),
                        "--seconds",
                        seconds,
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["feasible"], feasible)

    def test_nan_boolean_and_negative_budgets_are_rejected(self):
        for usd in (float("nan"), float("inf"), -1, True):
            with self.subTest(usd=usd), self.assertRaises(ValueError):
                Budget(usd, 10)


if __name__ == "__main__":
    unittest.main()
