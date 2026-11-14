import copy
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.compare import prepare
from benchmarks.qasper import (
    archive_path,
    build,
    candidates,
    document,
    read_split,
    reference,
    usable_question,
)


def paper(paper_id):
    evidence = f"Document {paper_id} reports a supported fact."
    annotations = []
    for index in range(3):
        answer = {
            "unanswerable": index == 2,
            "yes_no": False if index == 1 else None,
            "extractive_spans": [],
            "free_form_answer": "Reference-only value." if index == 0 else "",
            "evidence": [] if index == 2 else [evidence],
            "highlighted_evidence": [] if index == 2 else [evidence],
        }
        annotations.append(
            {
                "question_id": f"{paper_id}-question-{index}",
                "question": f"What does question {index} ask?",
                "question_writer": "do-not-export-worker",
                "answers": [
                    {
                        "answer": answer,
                        "worker_id": "do-not-export-worker",
                        "annotation_id": "unused",
                    }
                ],
            }
        )
    return {
        "title": f"Paper {paper_id}",
        "abstract": f"Abstract of {paper_id}.",
        "full_text": [
            {
                "section_name": "Results",
                "paragraphs": [evidence, "Retain this unrelated paragraph."],
            }
        ],
        "qas": annotations,
    }


class QasperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "dataset"
        self.data = {
            "train": {f"train{i}": paper(f"train{i}") for i in range(4)},
            "dev": {f"dev{i}": paper(f"dev{i}") for i in range(4)},
        }

    def build(self, output=None):
        return build(
            self.data, output or self.output, development_count=2, evaluation_count=2
        )

    def test_false_yes_no_and_unanswerable_questions_are_preserved(self):
        item = self.data["train"]["train0"]
        text = document(item)
        self.assertIsNone(usable_question(item["qas"][1], text))
        self.assertIsNone(usable_question(item["qas"][2], text))
        refs = reference(item["qas"][1])
        self.assertIs(refs["annotations"][0]["yes_no"], False)
        self.assertNotIn("do-not-export-worker", json.dumps(refs))

    def test_figure_missing_evidence_and_unusable_references_are_excluded(self):
        item = self.data["train"]["train0"]
        question = copy.deepcopy(item["qas"][0])
        question["answers"][0]["answer"]["evidence"] = ["FLOAT SELECTED: table caption"]
        self.assertEqual(usable_question(question, document(item)), "figure_or_table")
        question["answers"][0]["answer"]["evidence"] = ["Not present in the input."]
        self.assertEqual(
            usable_question(question, document(item)), "missing_text_evidence"
        )
        question["answers"] = []
        self.assertEqual(
            usable_question(question, document(item)), "missing_annotations"
        )

    def test_complete_body_is_retained_and_gold_annotations_are_not_in_model_sources(
        self,
    ):
        manifest = self.build()
        suite = json.loads((self.output / "suite.json").read_text())
        refs = json.loads((self.output / "references.json").read_text())
        self.assertEqual(len(suite["tasks"]), 4)
        for task in suite["tasks"]:
            text = (self.output / task["sources"][0]).read_text()
            self.assertIn("Retain this unrelated paragraph.", text)
            self.assertNotIn("Reference-only value.", text)
            self.assertIn("Reference-only value.", json.dumps(task["rubric"]))
            self.assertNotIn("do-not-export-worker", json.dumps(refs))
            self.assertEqual(
                {criterion["priority"] for criterion in task["criteria"]}, {1}
            )
        self.assertFalse(manifest["policy"]["official_test_used"])
        self.assertEqual(manifest["partitions"]["test"]["upstream_split"], "dev")
        self.assertIn("CC BY 4.0", (self.output / "ATTRIBUTION.md").read_text())

    def test_selection_is_deterministic_and_no_existing_dataset_is_overwritten(self):
        one = self.build()
        reordered = {
            split: dict(reversed(list(values.items())))
            for split, values in self.data.items()
        }
        two = build(
            reordered, self.root / "second", development_count=2, evaluation_count=2
        )
        self.assertEqual(one, two)
        with self.assertRaises(FileExistsError):
            self.build()

    def test_answerability_disagreement_is_preserved_instead_of_majority_labeled(self):
        for partition in self.data.values():
            for item in partition.values():
                extra = copy.deepcopy(item["qas"][2]["answers"][0])
                item["qas"][0]["answers"].append(extra)
        manifest = self.build()
        self.assertEqual(
            manifest["partitions"]["test"]["question_answerability"]["disagreement"], 2
        )
        references = json.loads((self.output / "references.json").read_text())
        variants = [
            question for task in references.values() for question in task["questions"]
        ]
        self.assertTrue(any(len(question["annotations"]) == 2 for question in variants))

    def test_duplicate_papers_do_not_leak_across_partitions(self):
        self.data["dev"] = copy.deepcopy(self.data["train"])
        with self.assertRaisesRegex(ValueError, "found only"):
            build(self.data, self.output, development_count=4, evaluation_count=1)
        self.assertFalse(self.output.exists())

    def test_insufficient_eligible_papers_fail_without_materializing_a_smaller_set(
        self,
    ):
        self.data["dev"] = {"too-few": paper("too-few")}
        with self.assertRaisesRegex(ValueError, "requested 2"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_download_is_opt_in_and_corrupted_caches_are_rejected(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            with self.assertRaisesRegex(ValueError, "--download"):
                archive_path(self.root)
            cache = self.root / "qasper-train-dev-v0.3.tgz"
            cache.write_bytes(b"bad cache")
            with self.assertRaisesRegex(ValueError, "pinned SHA-256"):
                archive_path(self.root)

    def test_explicit_download_checks_bytes_and_preserves_unrelated_partial_files(self):
        payload = b"Pinned archive fixture."
        unrelated = self.root / "qasper-train-dev-v0.3.tgz.partial"
        unrelated.write_bytes(b"Another download owns this file.")
        with (
            patch("urllib.request.urlopen", return_value=io.BytesIO(payload)),
            patch("benchmarks.qasper.SHA256", hashlib.sha256(payload).hexdigest()),
        ):
            path = archive_path(self.root, download=True)
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(unrelated.read_bytes(), b"Another download owns this file.")
        self.assertEqual(list(self.root.glob("*.partial")), [unrelated])

    def test_arxiv_version_aliases_cannot_cross_partitions(self):
        data = {
            "train": {"1801.00123v1": paper("first")},
            "dev": {"1801.00123v2": paper("second")},
        }
        with self.assertRaisesRegex(ValueError, "found only 0"):
            build(data, self.output, development_count=1, evaluation_count=1)
        self.assertFalse(self.output.exists())

    def test_archive_reader_ignores_paths_and_never_opens_official_test(self):
        archive = self.root / "tiny.tgz"
        with tarfile.open(archive, "w:gz") as stream:
            payload = json.dumps(self.data["dev"]).encode()
            member = tarfile.TarInfo("../qasper-dev-v0.3.json")
            member.size = len(payload)
            stream.addfile(member, io.BytesIO(payload))
        with self.assertRaisesRegex(ValueError, "bounded regular"):
            read_split(archive, "dev")
        with self.assertRaisesRegex(ValueError, "reserve official test"):
            read_split(archive, "test")
        self.assertFalse((self.root.parent / "qasper-dev-v0.3.json").exists())

    def test_candidate_metadata_reports_all_mechanical_exclusions(self):
        item = self.data["train"]["train0"]
        item["qas"][0]["answers"] = []
        eligible, exclusions = candidates({"one": item}, "train")
        self.assertEqual(eligible, [])
        self.assertEqual(exclusions["question:missing_annotations"], 1)
        self.assertEqual(exclusions["fewer_than_three_usable_questions"], 1)

    def test_generated_suite_is_accepted_by_the_existing_frozen_harness(self):
        self.build()
        models = Path(__file__).resolve().parents[1] / "src/spawn/templates/models.toml"
        report = prepare(
            self.output / "suite.json",
            models,
            self.root / "experiment",
            model="fast",
            repetitions=1,
        )
        self.assertEqual(report["cases"], 4 * 4 * 4)


if __name__ == "__main__":
    unittest.main()
