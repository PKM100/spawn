"""Reproduce a text-evidence QASPER task set without model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import tarfile
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path

from spawn.documents import MAX_DOCUMENT_BYTES

ARCHIVE = "qasper-train-dev-v0.3.tgz"
URL = f"https://qasper-dataset.s3.us-west-2.amazonaws.com/{ARCHIVE}"
SHA256 = "a28fdf966db827bcee3d873107d6b6669864fb7ca8fbf73a192f5e39191bdb5a"
CARD = (
    "https://huggingface.co/datasets/allenai/qasper/blob/"
    "fdc9d8214fbab5dd782958601db4d678e6934a54/README.md"
)
LIMIT = 100_000_000
POLICY = {
    "version": 1,
    "seed": 1729,
    "min_questions": 3,
    "max_questions": 6,
    "upstream_splits": {"development": "train", "test": "dev"},
    "official_test_used": False,
}
CONDITIONS = [
    {"id": "generous", "max_usd": 2, "max_seconds": 120},
    {"id": "moderate", "max_usd": 0.60, "max_seconds": 45},
    {"id": "tight", "max_usd": 0.30, "max_seconds": 15},
    {
        "id": "deadline-change",
        "max_usd": 2,
        "max_seconds": 120,
        "change": {"after_seconds": 5, "max_usd": 2, "max_seconds": 10},
    },
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def order(kind, value):
    return sha256(f"{POLICY['seed']}:{kind}:{value}".encode())


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def archive_path(cache, download=False):
    path = cache / ARCHIVE
    if not path.exists():
        if not download:
            raise ValueError(
                "Dataset cache is missing; pass --download to fetch public data"
            )
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=cache, prefix=ARCHIVE + ".", suffix=".partial", delete=False
        ) as stream:
            temporary = Path(stream.name)
        try:
            with temporary.open("wb") as stream:
                with urllib.request.urlopen(URL, timeout=60) as response:
                    total = 0
                    while chunk := response.read(1_000_000):
                        total += len(chunk)
                        if total > LIMIT:
                            raise ValueError(
                                "Dataset download exceeds the archive size limit"
                            )
                        stream.write(chunk)
            if sha256(temporary.read_bytes()) != SHA256:
                raise ValueError(
                    "Upstream archive hash changed; do not silently update the pin"
                )
            temporary.rename(path)
        finally:
            temporary.unlink(missing_ok=True)
    if path.stat().st_size > LIMIT or sha256(path.read_bytes()) != SHA256:
        raise ValueError("Cached QASPER archive does not match the pinned SHA-256")
    return path


def read_split(path, split):
    if split not in {"train", "dev"}:
        raise ValueError("Only original train/dev data is used; reserve official test")
    name = f"qasper-{split}-v0.3.json"
    with tarfile.open(path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.name == name]
        if len(members) != 1 or not members[0].isfile() or members[0].size > LIMIT:
            raise ValueError("Expected one bounded regular JSON member")
        with archive.extractfile(members[0]) as stream:
            return json.load(stream)


def document(paper):
    parts = [f"# {paper['title']}", "## Abstract", paper["abstract"]]
    for section in paper["full_text"]:
        parts += [f"## {section['section_name']}"] + section["paragraphs"]
    return "\n\n".join(parts).strip() + "\n"


def annotation_type(answer):
    if answer["unanswerable"]:
        return "unanswerable"
    if type(answer.get("yes_no")) is bool:
        return "yes_no"
    if answer.get("extractive_spans"):
        return "extractive"
    return "free_form"


def usable_question(question, text):
    if not question.get("question", "").strip() or not question.get("question_id"):
        return "missing_question"
    annotations = question.get("answers", [])
    if not annotations:
        return "missing_annotations"
    normalized = " ".join(text.split())
    for annotation in annotations:
        answer = annotation["answer"]
        evidence = answer.get("evidence", [])
        if any("FLOAT SELECTED" in item for item in evidence):
            return "figure_or_table"
        if type(answer.get("unanswerable")) is not bool:
            return "invalid_answerability"
        if answer["unanswerable"]:
            continue
        if not (
            answer.get("extractive_spans")
            or answer.get("free_form_answer", "").strip()
            or type(answer.get("yes_no")) is bool
        ):
            return "missing_answer"
        if not evidence or any(
            not item.strip() or " ".join(item.split()) not in normalized
            for item in evidence
        ):
            return "missing_text_evidence"
    return None


def candidates(data, split):
    eligible, exclusions = [], Counter()
    for paper_id, paper in data.items():
        text = document(paper)
        if not paper.get("full_text") or len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            exclusions["unusable_document"] += 1
            continue
        questions = []
        for question in paper["qas"]:
            reason = usable_question(question, text)
            if reason:
                exclusions[f"question:{reason}"] += 1
            else:
                questions.append(question)
        if len(questions) < POLICY["min_questions"]:
            exclusions["fewer_than_three_usable_questions"] += 1
            continue
        questions.sort(key=lambda question: order("question", question["question_id"]))
        eligible.append((paper_id, paper, text, questions[: POLICY["max_questions"]]))
    eligible.sort(key=lambda item: order(f"paper:{split}", item[0]))
    return eligible, dict(sorted(exclusions.items()))


def reference(question):
    keys = (
        "unanswerable",
        "extractive_spans",
        "yes_no",
        "free_form_answer",
        "evidence",
        "highlighted_evidence",
    )
    return {
        "question_id": question["question_id"],
        "annotations": [
            {key: annotation["answer"].get(key) for key in keys}
            for annotation in question["answers"]
        ],
    }


def build(data, output, *, development_count=20, evaluation_count=60):
    counts = {"development": development_count, "test": evaluation_count}
    if any(type(count) is not int or count < 1 for count in counts.values()):
        raise ValueError("Both partition counts must be positive integers")
    if output.exists():
        raise FileExistsError(
            "Task-set output already exists; preserve the existing selection"
        )
    tasks, selections, references, texts = [], [], {}, {}
    summary, paper_ids, titles, hashes, question_ids = {}, set(), set(), set(), set()
    for split, count in counts.items():
        upstream = POLICY["upstream_splits"][split]
        pool, exclusions = candidates(data[upstream], upstream)
        selected = []
        for paper_id, paper, text, questions in pool:
            title = " ".join(
                "".join(
                    c if c.isalnum() else " " for c in paper["title"].casefold()
                ).split()
            )
            canonical_id = re.sub(r"(?<=\d)v\d+$", "", paper_id)
            body_hash = sha256(text.encode("utf-8"))
            ids = [question["question_id"] for question in questions]
            if (
                canonical_id in paper_ids
                or title in titles
                or body_hash in hashes
                or set(ids) & question_ids
                or len(ids) != len(set(ids))
            ):
                exclusions["duplicate_selected_identity"] = (
                    exclusions.get("duplicate_selected_identity", 0) + 1
                )
                continue
            paper_ids.add(canonical_id)
            titles.add(title)
            hashes.add(body_hash)
            question_ids.update(ids)
            task_id = "qasper-" + sha256(paper_id.encode())[:16]
            source = f"papers/{task_id}.txt"
            criteria, rubric = [], {}
            refs = [reference(question) for question in questions]
            for question, ref in zip(questions, refs, strict=True):
                cid = "q-" + sha256(question["question_id"].encode())[:20]
                criteria.append(
                    {"id": cid, "description": question["question"], "priority": 1}
                )
                rubric[cid] = (
                    "Judge correctness, semantic evidence support and completeness "
                    "against the full paper. "
                    "References are human annotations, "
                    "not automatically verified truth. "
                    "Accept correct paraphrases. For unanswerable questions, "
                    "omission can be complete. "
                    "Adjudicate conflicting annotations before evaluating "
                    "policy outputs. "
                    "Reference variants: "
                    + json.dumps(ref, ensure_ascii=False, sort_keys=True)
                )
            task = {
                "id": task_id,
                "family": "research-paper",
                "group": task_id,
                "split": split,
                "origin": "external",
                "provenance": (
                    f"QASPER v0.3, original {upstream} split, paper {paper_id}. "
                    f"Dataset: CC BY 4.0; {CARD}. "
                    f"Paper: https://arxiv.org/abs/{paper_id}. "
                    "Body formatting and multi-question bundling adapted by Spawn; "
                    "see ATTRIBUTION.md."
                ),
                "goal": (
                    "Answer each question using only the supplied research paper. "
                    "Cite exact supporting quotes. "
                    "Omit findings the paper cannot answer."
                ),
                "sources": [source],
                "criteria": criteria,
                "rubric": rubric,
            }
            tasks.append(task)
            texts[source] = text
            references[task_id] = {
                "paper_id": paper_id,
                "title": paper["title"],
                "questions": refs,
            }
            selection = {
                "task_id": task_id,
                "paper_id": paper_id,
                "split": split,
                "upstream_split": upstream,
                "question_ids": ids,
                "source_sha256": body_hash,
                "source_bytes": len(text.encode("utf-8")),
            }
            selections.append(selection)
            selected.append((selection, refs))
            if len(selected) == count:
                break
        if len(selected) != count:
            raise ValueError(
                f"{split}: requested {count} papers, "
                f"found only {len(selected)} eligible disjoint papers"
            )
        answerability, kinds = Counter(), Counter()
        for _, refs in selected:
            for ref in refs:
                labels = {answer["unanswerable"] for answer in ref["annotations"]}
                category = (
                    "disagreement"
                    if len(labels) > 1
                    else "unanswerable"
                    if True in labels
                    else "answerable"
                )
                answerability[category] += 1
                kinds.update(annotation_type(answer) for answer in ref["annotations"])
        sizes = [item["source_bytes"] for item, _ in selected]
        summary[split] = {
            "upstream_split": upstream,
            "papers": len(selected),
            "questions": sum(len(item["question_ids"]) for item, _ in selected),
            "eligible_papers_in_pool": len(pool),
            "exclusions": exclusions,
            "question_answerability": dict(answerability),
            "annotation_answer_types": dict(kinds),
            "source_bytes": {
                "min": min(sizes),
                "median": statistics.median(sizes),
                "max": max(sizes),
            },
            "questions_per_paper": dict(
                sorted(
                    Counter(
                        str(len(item["question_ids"])) for item, _ in selected
                    ).items()
                )
            ),
        }
    manifest = {
        "dataset": "QASPER v0.3",
        "archive_url": URL,
        "archive_sha256": SHA256,
        "importer_sha256": sha256(Path(__file__).read_bytes()),
        "license": "CC-BY-4.0 (upstream dataset card)",
        "license_source": CARD,
        "policy": POLICY,
        "partitions": summary,
        "selected": selections,
        "warning": (
            "Spawn adaptation, not official QASPER scores. "
            "Public data may be in model training."
        ),
    }
    suite = {
        "id": "qasper-text-v1",
        "description": (
            "External research-paper QA adaptation; original train -> development, "
            "original dev -> held-out test. No official QASPER test data. "
            "Budget conditions are placeholders, not calibrated operating points."
        ),
        "conditions": CONDITIONS,
        "tasks": tasks,
    }
    output.mkdir(parents=True)
    (output / "papers").mkdir()
    for name, text in texts.items():
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    write_json(output / "suite.json", suite)
    write_json(output / "references.json", references)
    write_json(output / "selection.json", manifest)
    attribution = [
        "# QASPER data attribution",
        "",
        "A Dataset of Information-Seeking Questions and Answers "
        "Anchored in Research Papers.",
        "Pradeep Dasigi, Kyle Lo, Iz Beltagy, Arman Cohan, "
        "Noah A. Smith, Matt Gardner (2021).",
        "https://arxiv.org/abs/2105.03011",
        f"Dataset license source: {CARD}",
        "CC BY 4.0: https://creativecommons.org/licenses/by/4.0/",
        "",
        "Changes: plain-text body rendering, text-evidence eligibility filtering, "
        "deterministic paper/question selection, and multi-question task bundling.",
        "This data is not covered by the Spawn code's MIT license. "
        "Individual paper rights remain with their respective holders. "
        "No endorsement by the dataset or paper authors is implied.",
        "",
        "## Paper sources",
        "",
    ]
    attribution += [
        f"- {references[item['task_id']]['title']} -- https://arxiv.org/abs/{item['paper_id']}"
        for item in selections
    ]
    (output / "ATTRIBUTION.md").write_text(
        "\n".join(attribution) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path(".spawn/datasets/cache"))
    parser.add_argument(
        "--output", type=Path, default=Path(".spawn/datasets/qasper-v1")
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Allow downloading the pinned public archive",
    )
    parser.add_argument("--development-count", type=int, default=20)
    parser.add_argument("--evaluation-count", type=int, default=60)
    parser.add_argument(
        "--selection-manifest",
        type=Path,
        help="Also write content-free selection metadata for version control",
    )
    args = parser.parse_args(argv)
    try:
        if args.selection_manifest is not None and args.selection_manifest.exists():
            raise FileExistsError("Selection manifest already exists")
        path = archive_path(args.cache.resolve(), args.download)
        data = {split: read_split(path, split) for split in ("train", "dev")}
        manifest = build(
            data,
            args.output.resolve(),
            development_count=args.development_count,
            evaluation_count=args.evaluation_count,
        )
        if args.selection_manifest is not None:
            write_json(args.selection_manifest, manifest)
        print(
            json.dumps(
                {key: value for key, value in manifest.items() if key != "selected"},
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        parser.exit(2, f"QASPER preparation error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
