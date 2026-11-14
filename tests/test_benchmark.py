import unittest
from pathlib import Path

from benchmarks.run_pilot import PROFILE, FixedPlanner, score
from spawn.contracts import Budget, Criterion, Document, Permissions, Request


class PilotScoringTests(unittest.TestCase):
    def test_supported_answer_requires_every_rubric_pattern(self):
        task = {"rubric": {"answer": ["18", "6"]}}
        result = {
            "status": "evidence_ready",
            "findings": [{"criterion_id": "answer", "answer": "It used 18 calls."}],
        }
        checked = score(task, result)
        self.assertFalse(checked["rubric_pass"])
        self.assertTrue(checked["false_evidence_ready"])

    def test_abstention_passes_only_when_unsupported_finding_is_omitted(self):
        task = {"rubric": {"unsupported": None}}
        result = {"status": "partial", "findings": []}
        self.assertTrue(score(task, result)["rubric_pass"])
        result["findings"] = [{"criterion_id": "unsupported", "answer": "A guess"}]
        self.assertFalse(score(task, result)["rubric_pass"])


class BaselineTests(unittest.TestCase):
    def setUp(self):
        source = Path("unused-fixture.txt")
        self.documents = [Document("S1", source, "Public fixture notes.", "fixture")]
        self.request = Request(
            "Describe the notes.",
            (source,),
            (Criterion("a", "First fact"), Criterion("b", "Second fact")),
            Budget(2, 120),
            Permissions((Path("."),), Path(".spawn/tests"), (PROFILE.name,), True),
        )

    def test_baselines_really_use_different_call_groupings(self):
        single = FixedPlanner("single_pass").plan(self.request, self.documents)
        incremental = FixedPlanner("per_criterion").plan(self.request, self.documents)
        self.assertEqual([c.criteria for c in single.calls], [("a", "b")])
        self.assertEqual([c.criteria for c in incremental.calls], [("a",), ("b",)])

    def test_fixed_baseline_refuses_infeasible_work_instead_of_ignoring_budget(self):
        from dataclasses import replace

        request = replace(self.request, budget=Budget(0.30, 120))
        plan = FixedPlanner("per_criterion").plan(request, self.documents)
        self.assertFalse(plan.feasible)
        self.assertEqual(plan.deferred, ["a", "b"])
