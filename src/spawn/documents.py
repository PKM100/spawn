import hashlib
import re

from .contracts import Document, Request

MAX_DOCUMENT_BYTES = 2_000_000
MAX_CORPUS_BYTES = 10_000_000
TEXT_SUFFIXES = {".txt", ".md", ".rst", ".csv", ".json"}


def load_documents(request: Request) -> list[Document]:
    documents = []
    seen = set()
    total = 0
    for source in request.sources:
        path = request.permissions.readable(source)
        if path in seen:
            continue
        seen.add(path)
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            raise ValueError(f"Unsupported text document: {path}")
        with path.open("rb") as stream:
            data = stream.read(MAX_DOCUMENT_BYTES + 1)
        total += len(data)
        if len(data) > MAX_DOCUMENT_BYTES or total > MAX_CORPUS_BYTES:
            raise ValueError("Document or corpus exceeds the local ingestion limit")
        text = data.decode("utf-8")
        if not text.strip():
            raise ValueError(f"Empty document: {path}")
        documents.append(
            Document(
                f"S{len(documents) + 1}", path, text, hashlib.sha256(data).hexdigest()
            )
        )
    return documents


def select_excerpts(
    documents: list[Document], query: str, *, chars_per_source: int
) -> dict[str, str]:
    terms = set(re.findall(r"\w{3,}", query.lower()))
    excerpts = {}
    for document in documents:
        chunks = [
            document.text[i : i + 1800] for i in range(0, len(document.text), 1800)
        ]
        ranked = sorted(
            enumerate(chunks),
            key=lambda item: (
                -len(terms & set(re.findall(r"\w{3,}", item[1].lower()))),
                item[0],
            ),
        )
        selected = []
        remaining = chars_per_source
        for index, chunk in ranked:
            if remaining <= 0:
                break
            selected.append((index, chunk[:remaining]))
            remaining -= len(chunk[:remaining])
        excerpts[document.id] = "\n[...]\n".join(chunk for _, chunk in sorted(selected))
    return excerpts
