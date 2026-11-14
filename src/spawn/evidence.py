import json

from .contracts import Call, Criterion, Document


def validate_findings(
    text: str,
    call: Call,
    criteria: tuple[Criterion, ...],
    documents: list[Document],
) -> tuple[list[dict], list[str]]:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(stripped)
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("Expected a JSON object with a findings list")
    specs = {c.id: c for c in criteria if c.id in call.criteria}
    sources = {d.id: d.text for d in documents}
    accepted = []
    seen = set()
    errors = []
    for finding in data["findings"]:
        if not isinstance(finding, dict):
            errors.append("Rejected non-object finding")
            continue
        cid = finding.get("criterion_id")
        if not isinstance(cid, str) or cid not in specs or cid in seen:
            errors.append("Rejected unknown or duplicate criterion")
            continue
        answer = finding.get("answer")
        evidence = finding.get("evidence")
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or not isinstance(evidence, list)
        ):
            errors.append(f"{cid}: missing answer or evidence")
            continue
        checked = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            sid, quote = item.get("source_id"), item.get("quote")
            if (
                isinstance(sid, str)
                and isinstance(quote, str)
                and quote.strip()
                and quote in call.excerpts.get(sid, "")
                and quote in sources.get(sid, "")
            ):
                checked.append({"source_id": sid, "quote": quote})
        if len({item["source_id"] for item in checked}) < specs[cid].min_sources:
            errors.append(f"{cid}: insufficient exact-quote evidence")
            continue
        accepted.append({"criterion_id": cid, "answer": answer, "evidence": checked})
        seen.add(cid)
    return accepted, errors
