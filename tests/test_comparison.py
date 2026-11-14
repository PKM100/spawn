import contextlib
import io
import json
import tempfile
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from benchmarks.compare import (
    Baseline,
    Meter,
    execute,
    frozen,
    main,
    packet_for,
    prepare,
    read_json,
    records_for,
    summarize,
    write_json,
)
from spawn.contracts import Budget, Criterion, Model, Permissions, Request
from spawn.documents import load_documents
from spawn.planner import make_call
from spawn.providers import ProviderError, Response


def profile(**changes):
    return replace(
        Model(
            "test",
            "openai",
            "fake",
            "https://example.invalid/v1",
            1,
            4,
            1e9,
            1e9,
            0,
            context_tokens=64000,
        ),
        **changes,
    )


class FixtureProvider:
    def __init__(self, *, delay=0, failure=None, unknown=False):
        self.delay, self.failure, self.unknown = delay, failure, unknown
        self.calls = []

    def complete(self, model, call, timeout):
        self.calls.append(call)
        if self.failure:
            raise self.failure
        time.sleep(self.delay)
        prompt = json.loads(call.prompt)
        evidence = [
            {
                "source_id": document["source_id"],
                "quote": document["text"].splitlines()[0],
            }
            for document in prompt["documents"]
        ]
        return Response(
            json.dumps(
                {
                    "findings": [
                        {
                            "criterion_id": criterion["id"],
                            "answer": "Fixture answer.",
                            "evidence": evidence,
                        }
                        for criterion in prompt["criteria"]
                    ]
                }
            ),
            None if self.unknown else 10,
            None if self.unknown else 10,
        )


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "source.txt").write_text(
            "Fixture fact.\nAnother fact.", encoding="utf-8"
        )
        (self.root / "second.txt").write_text("Independent fixture.", encoding="utf-8")
        self.suite = {
            "id": "test-suite",
            "description": "Authored harness tests, not evidence.",
            "conditions": [{"id": "normal", "max_usd": 1, "max_seconds": 120}],
            "tasks": [
                {
                    "id": name,
                    "family": "extraction",
                    "group": name,
                    "split": "test",
                    "origin": "authored",
                    "provenance": "Authored unit-test fixture, MIT.",
                    "goal": "Describe the fixture.",
                    "sources": [source],
                    "criteria": [
                        {"id": "a", "description": "First fact", "priority": 3},
                        {"id": "b", "description": "Second fact", "priority": 1},
                    ],
                    "rubric": {"a": "Correct first fact.", "b": "Correct second fact."},
                }
                for name, source in (("one", "source.txt"), ("two", "second.txt"))
            ],
        }
        self.suite_path = self.root / "suite.json"
        self.models_path = self.root / "models.toml"
        self.output = self.root / "experiment"
        self.write_profiles([profile()])

    def write_profiles(self, profiles):
        sections = []
        for model in profiles:
            sections.append(
                "[[models]]\n"
                + "\n".join(
                    f"{key} = {json.dumps(value)}"
                    for key, value in asdict(model).items()
                    if value is not None
                )
            )
        self.models_path.write_text("\n".join(sections), encoding="utf-8")

    def prepare(self, **kwargs):
        write_json(self.suite_path, self.suite)
        return prepare(
            self.suite_path,
            self.models_path,
            self.output,
            repetitions=kwargs.pop("repetitions", 1),
            **kwargs,
        )

    def run_live_fixture(self, provider=None, **kwargs):
        return execute(
            self.output,
            live=True,
            approve_sharing=True,
            max_usd=kwargs.pop("max_usd", 100),
            max_calls=kwargs.pop("max_calls", 100),
            provider=provider or FixtureProvider(),
            **kwargs,
        )

    def reviewed_packet(self):
        plan = frozen(self.output)
        packet = packet_for(plan, records_for(self.output, plan), self.output)
        packet["reviewer_alias"] = "independent-test-rater"
        for item in packet["items"]:
            for judgment in item["judgments"].values():
                judgment.update(dict.fromkeys(judgment, True))
            item["usefulness"] = 4
        path = self.root / "review.json"
        write_json(path, packet)
        return path, packet

    def test_prepare_freezes_sources_profiles_schedule_and_checks_integrity(self):
        report = self.prepare(repetitions=2)
        self.assertEqual(report["cases"], 16)
        plan = frozen(self.output, check_code=True)
        self.assertEqual(
            {arm["policy"] for arm in plan["arms"]},
            {"spawn", "single_pass", "per_criterion", "budget_aware"},
        )
        (self.root / "source.txt").write_text("Changed original.", encoding="utf-8")
        frozen(self.output)
        snapshot = self.output / plan["tasks"][0]["sources"][0]
        snapshot.write_text("Tampered snapshot.", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            frozen(self.output)

    def test_prepare_rejects_split_and_document_group_leakage(self):
        self.suite["tasks"][1]["sources"] = ["source.txt"]
        with self.assertRaisesRegex(ValueError, "same split and related group"):
            self.prepare()
        self.suite["tasks"][1]["sources"] = ["second.txt"]
        self.suite["tasks"][1]["group"] = "one"
        self.suite["tasks"][1]["split"] = "development"
        with self.assertRaisesRegex(ValueError, "groups cannot cross"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_split_selection_never_freezes_other_partition_sources_or_reference_answers(
        self,
    ):
        self.suite["tasks"][0]["split"] = "development"
        self.prepare(split="development")
        plan = frozen(self.output)
        self.assertEqual(len(plan["tasks"]), 1)
        self.assertEqual(plan["tasks"][0]["id"], "one")
        self.assertEqual({case["task"] for case in plan["cases"]}, {"one"})
        self.assertEqual(len(plan["files"]), 1)
        self.assertNotIn("Independent fixture.", json.dumps(plan))
        copied = [path.read_text() for path in (self.output / "sources").rglob("*.txt")]
        self.assertFalse(any("Independent fixture." in text for text in copied))

    def test_invalid_changes_and_duplicate_tasks_are_rejected_before_writes(self):
        self.suite["conditions"][0]["change"] = {
            "after_seconds": 2,
            "max_usd": 1,
            "max_seconds": 200,
        }
        with self.assertRaisesRegex(ValueError, "shorten"):
            self.prepare()
        del self.suite["conditions"][0]["change"]
        self.suite["tasks"][1]["id"] = "one"
        with self.assertRaisesRegex(ValueError, "unique IDs"):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_selection_runs_each_fixed_profile_and_shares_pool_with_adaptive_baseline(
        self,
    ):
        self.write_profiles([profile(), profile(name="other")])
        with self.assertRaisesRegex(ValueError, "--model"):
            self.prepare()
        self.prepare(experiment="selection")
        plan = frozen(self.output)
        self.assertEqual(len(plan["arms"]), 6)
        self.assertEqual(plan["arms"][-1]["models"], ["test", "other"])
        self.assertEqual(
            {case["arm"] for case in plan["cases"]}, {arm["id"] for arm in plan["arms"]}
        )

    def test_baselines_batch_queue_and_fit_partial_work_without_ignoring_limits(self):
        request = Request(
            "Facts",
            (self.root / "source.txt",),
            (Criterion("a", "First", 3), Criterion("b", "Second")),
            Budget(1, 120),
            Permissions((self.root,), self.root / "runs", ("test",), True),
        )
        documents = load_documents(request)
        single = Baseline([profile()], "single_pass").plan(request, documents)
        queue = Baseline([profile()], "per_criterion").plan(request, documents)
        self.assertEqual([call.criteria for call in single.calls], [("a", "b")])
        self.assertEqual([call.criteria for call in queue.calls], [("a",), ("b",)])
        one = make_call(
            request, request.criteria[:1], documents, profile(), "single_pass"
        )
        tight = replace(request, budget=Budget(one.cost_high + 1e-12, 120))
        greedy = Baseline([profile()], "budget_aware").plan(tight, documents)
        self.assertEqual(greedy.deferred, ["b"])
        self.assertFalse(
            Baseline([profile()], "single_pass").plan(tight, documents).feasible
        )
        self.assertEqual(
            len(Baseline([profile()], "per_criterion").plan(tight, documents).calls), 1
        )

    def test_synthetic_workflow_never_calls_network_or_produces_performance_claims(
        self,
    ):
        self.prepare()
        with patch(
            "spawn.providers.HTTPProvider.complete",
            side_effect=AssertionError("network"),
        ):
            status = execute(self.output)
        self.assertTrue(status["complete"])
        self.assertTrue(status["simulation"])
        path, _ = self.reviewed_packet()
        summary = summarize(self.output, path, bootstrap_samples=20)
        self.assertTrue(summary["complete_review"])
        self.assertTrue(
            all(group["primary_success_rate"] is None for group in summary["groups"])
        )
        self.assertEqual(summary["paired_comparisons"], [])
        with self.assertRaises(FileExistsError):
            execute(self.output)

    def test_live_requires_explicit_sharing_and_aggregate_caps(self):
        self.prepare()
        with patch(
            "spawn.providers.HTTPProvider.complete",
            side_effect=AssertionError("network"),
        ):
            with self.assertRaisesRegex(ValueError, "requires"):
                execute(self.output, live=True)
        self.assertFalse((self.output / "started.json").exists())
        with patch.dict("os.environ", {"SPAWN_ACTIVE": "1"}):
            with self.assertRaisesRegex(ValueError, "Nested"):
                self.run_live_fixture()

    def test_reservation_overrun_halts_before_another_call(self):
        request = Request(
            "Facts",
            (self.root / "source.txt",),
            (Criterion("a", "First"),),
            Budget(1, 120),
            Permissions((self.root,), self.root / "runs", ("test",), True),
        )
        call = make_call(
            request, request.criteria, load_documents(request), profile(), "single_pass"
        )
        journal = self.root / "calls.jsonl"
        meter = Meter(FixtureProvider(), 100, 10, journal=journal)
        with patch.object(
            meter.provider,
            "complete",
            return_value=Response(
                '{"findings": []}',
                1,
                1,
                reported_cost_usd=call.cost_high * 2,
            ),
        ) as complete:
            meter.complete(profile(), call, 120)
            with self.assertRaisesRegex(ProviderError, "exceeded"):
                meter.complete(profile(), call, 120)
            self.assertEqual(complete.call_count, 1)
        self.assertAlmostEqual(meter.cost, call.cost_high * 2)
        events = [
            json.loads(line)["event"] for line in journal.read_text().splitlines()
        ]
        self.assertEqual(events, ["reserved", "returned"])

    def test_aggregate_gate_stops_before_unapproved_spend_and_missing_runs_stay_missing(
        self,
    ):
        self.prepare()
        provider = FixtureProvider()
        status = self.run_live_fixture(provider, max_usd=0)
        self.assertEqual(len(provider.calls), 0)
        self.assertFalse(status["complete"])
        self.assertIn("Aggregate", status["halt_reason"])
        path, _ = self.reviewed_packet()
        summary = summarize(self.output, path, bootstrap_samples=20)
        self.assertTrue(
            all(group["primary_success_rate"] is None for group in summary["groups"])
        )
        self.assertFalse(summary["complete_execution"])

    def test_provider_failure_or_unknown_billing_halts_without_a_second_paid_attempt(
        self,
    ):
        for kwargs in ({"failure": ProviderError("failed")}, {"unknown": True}):
            with self.subTest(kwargs=kwargs):
                self.output = self.root / (
                    "failed" if "failure" in kwargs else "unknown"
                )
                self.prepare()
                provider = FixtureProvider(**kwargs)
                status = self.run_live_fixture(provider)
                self.assertEqual(len(provider.calls), 1)
                self.assertFalse(status["complete"])
                self.assertGreater(status["accounted_cost_usd"], 0)
                self.assertIsNotNone(status["halt_reason"])
                record = records_for(self.output, frozen(self.output))[0]
                self.assertFalse(record["billing_known"])
                events = (self.output / "provider-calls.jsonl").read_text()
                self.assertIn('"event": "reserved"', events)

    def test_blind_packet_omits_policy_billing_paths_and_execution_metadata(self):
        self.prepare()
        self.run_live_fixture()
        path, packet = self.reviewed_packet()
        item = packet["items"][0]
        for key in ("arm", "policy", "model", "calls", "cost", "status", "artifacts"):
            self.assertNotIn(key, item)
        self.assertNotIn(str(self.output), json.dumps(packet))
        item["findings"][0]["answer"] = "Edited answer."
        write_json(path, packet)
        with self.assertRaisesRegex(ValueError, "content changed"):
            summarize(self.output, path, bootstrap_samples=20)

    def test_full_original_task_is_graded_not_only_scheduled_findings(self):
        self.prepare(repetitions=2)
        self.run_live_fixture()
        path, packet = self.reviewed_packet()
        for item in packet["items"]:
            item["judgments"]["b"]["complete"] = False
            item["usefulness"] = 2
        write_json(path, packet)
        summary = summarize(self.output, path, bootstrap_samples=30)
        self.assertTrue(summary["complete_review"])
        for group in summary["groups"]:
            self.assertEqual(group["primary_success_rate"], 0)
            self.assertEqual(group["weighted_completion"], 0.75)
            self.assertIsNone(group["cost_per_success_usd"])
        for pair in summary["paired_comparisons"]:
            self.assertEqual(pair["independent_groups_declared"], 2)
            self.assertEqual(
                pair["metrics"]["primary_success_rate"]["spawn_minus_baseline"], 0
            )

    def test_pending_judgments_do_not_become_success_or_failure_rates(self):
        self.prepare()
        self.run_live_fixture()
        path, packet = self.reviewed_packet()
        for item in packet["items"]:
            item["judgments"]["a"]["correct"] = None
        write_json(path, packet)
        summary = summarize(self.output, path, bootstrap_samples=20)
        self.assertFalse(summary["complete_review"])
        self.assertTrue(
            all(group["primary_success_rate"] is None for group in summary["groups"])
        )
        self.assertEqual(summary["paired_comparisons"], [])

    def test_correct_abstention_can_complete_an_unsupported_original_criterion(self):
        self.suite["tasks"] = self.suite["tasks"][:1]
        task = self.suite["tasks"][0]
        task["criteria"] = [{"id": "missing", "description": "Omit unsupported dates."}]
        task["rubric"] = {
            "missing": "No date is supported. Correct omission is complete."
        }
        self.prepare()
        provider = FixtureProvider()
        with patch.object(
            provider, "complete", return_value=Response('{"findings": []}', 10, 10)
        ):
            self.run_live_fixture(provider)
        path, packet = self.reviewed_packet()
        self.assertTrue(all(not item["findings"] for item in packet["items"]))
        summary = summarize(self.output, path, bootstrap_samples=20)
        self.assertTrue(
            all(group["primary_success_rate"] == 1 for group in summary["groups"])
        )
        self.assertTrue(
            all(
                pair["metrics"]["primary_success_rate"][
                    "cluster_bootstrap_95_percentile"
                ]
                is None
                for pair in summary["paired_comparisons"]
            )
        )

    def test_wall_clock_deadline_change_counts_slow_inflight_calls_as_over_limit(self):
        self.suite["tasks"] = self.suite["tasks"][:1]
        self.suite["conditions"] = [
            {
                "id": "shorten",
                "max_usd": 1,
                "max_seconds": 120,
                "change": {"after_seconds": 0.001, "max_usd": 1, "max_seconds": 0.01},
            }
        ]
        self.prepare()
        self.run_live_fixture(FixtureProvider(delay=0.03))
        rows = records_for(self.output, frozen(self.output))
        self.assertTrue(all("at_seconds" in row["change_applied"] for row in rows))
        self.assertTrue(all(not row["within_limits"] for row in rows))
        self.assertTrue(
            all(row["effective_limits"]["max_seconds"] == 0.01 for row in rows)
        )

    def test_review_and_execution_tampering_are_rejected(self):
        self.prepare()
        self.run_live_fixture()
        path, packet = self.reviewed_packet()
        packet["items"].append(packet["items"][0])
        write_json(path, packet)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            summarize(self.output, path, bootstrap_samples=20)
        records = self.output / "records.jsonl"
        records.write_text(
            records.read_text() + records.read_text().splitlines()[0] + "\n"
        )
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            records_for(self.output, frozen(self.output))

    def test_success_metrics_include_unsuccessful_cost_and_cluster_not_repetitions(
        self,
    ):
        self.prepare(repetitions=2)
        self.run_live_fixture()
        path, packet = self.reviewed_packet()
        # Fail the same task across all policies/repetitions, retaining its cost.
        for item in packet["items"]:
            if item["sources"][0]["text"].startswith("Fixture fact."):
                item["judgments"]["a"]["correct"] = False
        write_json(path, packet)
        summary = summarize(self.output, path, bootstrap_samples=30)
        for group in summary["groups"]:
            self.assertEqual(group["primary_success_rate"], 0.5)
            self.assertAlmostEqual(
                group["cost_per_success_usd"], group["total_accounted_usd"] / 2
            )
        self.assertTrue(
            all(
                pair["independent_groups_declared"] == 2
                for pair in summary["paired_comparisons"]
            )
        )

    def test_cli_exports_review_and_summarizes_without_inference(self):
        self.prepare()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch(
                "spawn.providers.HTTPProvider.complete",
                side_effect=AssertionError("network"),
            ),
        ):
            self.assertEqual(main(["run", "--output", str(self.output)]), 0)
            packet = self.root / "packet.json"
            self.assertEqual(
                main(
                    [
                        "export-review",
                        "--output",
                        str(self.output),
                        "--packet",
                        str(packet),
                    ]
                ),
                0,
            )
            self.assertEqual(main(["summarize", "--output", str(self.output)]), 0)
        self.assertTrue(packet.exists())
        self.assertTrue(read_json(self.output / "summary.json")["simulation"])

    def test_cli_reports_incomplete_execution_without_starting_unfunded_calls(self):
        self.prepare()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            patch(
                "spawn.providers.HTTPProvider.complete",
                side_effect=AssertionError("network"),
            ),
        ):
            status = main(
                [
                    "run",
                    "--output",
                    str(self.output),
                    "--live",
                    "--approve-sharing",
                    "--max-total-usd",
                    "0",
                    "--max-calls",
                    "1",
                ]
            )
        self.assertEqual(status, 3)
        self.assertFalse(read_json(self.output / "execution.json")["complete"])


if __name__ == "__main__":
    unittest.main()
