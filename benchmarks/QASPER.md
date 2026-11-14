# First external task set: QASPER

QASPER contains research-paper questions written and answered by NLP practitioners,
with reference answers, evidence and unanswerable annotations. We adapt it into
multi-criterion document-research tasks. This is **not the original QASPER scoring
protocol**, a new independent dataset, or evidence that Spawn performs well.

Sources:

- [Official dataset and CC BY 4.0 dataset card](https://huggingface.co/datasets/allenai/qasper).
- [Dataset card revision](https://huggingface.co/datasets/allenai/qasper/blob/fdc9d8214fbab5dd782958601db4d678e6934a54/README.md).
- [Dasigi et al., 2021](https://arxiv.org/abs/2105.03011):
  *A Dataset of Information-Seeking Questions and Answers Anchored in Research Papers*.
- [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## Selection policy v1

This policy is fixed before sampling or model evaluation:

1. Use QASPER v0.3's original training split for **20 development papers** and
   original development split for **60 held-out evaluation papers**. The latter
   use the harness label `test`, not QASPER's official test split. Reserve the
   official test split for a finalized-system replication, as upstream requests.
   Never select by model outputs.
2. Keep the title, abstract and all body sections as one local UTF-8 document.
   Do not crop to annotated evidence. Figures, tables and their captions are not
   reconstructed from images.
3. Keep questions with nonempty human reference annotations. Answerable annotations
   must have text evidence present in the supplied body/abstract and a valid
   extractive, free-form or yes/no answer. Exclude questions with figure/table
   evidence or missing textual evidence; report every exclusion reason.
   Preserve answerability disagreements and all reference variants for retained
   questions. They need human adjudication, not automatic majority labels.
4. Require at least three eligible questions per paper. Order papers and questions
   by SHA-256 of their IDs with fixed seed **1729**, independently of answer type,
   difficulty, expected answer and model performance. Take the first eligible
   papers, and at most six questions per paper.
5. Keep original question IDs and equal criterion priorities. One paper is one
   related sampling group. Reject duplicate paper IDs, normalized titles, source
   hashes or question IDs across the selected splits.
6. Preserve source archive hashes, selected IDs, annotation variants, body hashes,
   exclusions, answer types, answerability disagreements and document lengths.
   Never resample because a model does poorly or an observed effect is small.

Eligibility defines a **text-evidence subset**, not the full original distribution.
The set tests research-paper question answering, long documents and abstention.
It does not cover cross-document synthesis, contradictory document versions,
general tool agents or arbitrary fields. Public 2021 data may be in model training:
`test` means withheld from Spawn tuning, **not proven unseen by the model**.

## Local data and reproduction

| Spawn partition | Original QASPER split | Papers/tasks | Questions/criteria |
| --- | --- | ---: | ---: |
| Development | Train | 20 | 80 |
| Held-out evaluation (`test`) | Development | 60 | 240 |

Each task has 3–6 questions. Held-out annotations contain 213 consistently answerable
questions, 15 consistently unanswerable questions and **12 with answerability
disagreement**. Resolve those disagreements through independent review; do not
silently discard them. These are dataset characteristics, not Spawn results.

From the development install described in CONTRIBUTING.md:

```sh
.venv/bin/python benchmarks/qasper.py --download
cmp .spawn/datasets/qasper-v1/selection.json benchmarks/qasper-v1.selection.json
```

The importer downloads only the original HTTPS train/dev archive and checks its
pinned SHA-256 hash. It reads named JSON members without extracting archive paths.
It does not install dataset packages, execute downloaded code, or call a model.
Existing output directories are never overwritten.

Generated paper text, questions and references stay under ignored `.spawn/`.
They are not relicensed under this repository's MIT code license or committed
as project-authored fixtures. The output includes dataset attribution, paper
source links and a separate data-license notice. Retain them if sharing data;
individual paper rights remain with their respective holders.

`.spawn/datasets/qasper-v1/` contains the harness-compatible `suite.json`, full
`papers/`, sanitized `references.json`, `selection.json`, and `ATTRIBUTION.md`.
The committed selection manifest contains IDs, hashes and aggregate counts,
not paper text, answers or annotator identities.

To check the development pipeline **synthetically**, without paid calls:

```sh
.venv/bin/python benchmarks/compare.py prepare \
  --suite .spawn/datasets/qasper-v1/suite.json --split development \
  --models src/spawn/templates/models.toml --model fast \
  --repetitions 1 --output .spawn/qasper-development-smoke
.venv/bin/python benchmarks/compare.py run --output .spawn/qasper-development-smoke
```

Only use `--split test` after finalizing the policy, profiles and conditions using
development data. Freeze the complete study before any held-out live inference.

The generated `suite.json` is compatible with `compare.py`. Its example limits
are placeholders: calibrate model profiles and budget/deadline conditions on the
development set **before** freezing a test study. Six criteria require at least
4,500 configured output tokens under the current 750-tokens-per-criterion planner;
check baseline feasibility rather than manufacturing a win with unsuitable limits.
The current runtime selects lexical excerpts, not full-paper model context.

Independent human review must check references and resolve annotation conflicts
without seeing policy outputs. The importer provides review guidance, not verified
semantic ground truth. Downloading this task set does not authorize paid inference.
