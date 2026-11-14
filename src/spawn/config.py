import json
import tomllib
from pathlib import Path

from .contracts import Budget, Criterion, Model, Permissions, Request


def load_models(path: Path) -> list[Model]:
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    return [Model(**entry) for entry in data["models"]]


def load_request(path: Path) -> Request:
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent

    def resolve(value: str) -> Path:
        return (base / value).resolve()

    permissions = data.pop("permissions")
    control_file = data.pop("control_file", None)
    return Request(
        goal=data.pop("goal"),
        sources=tuple(resolve(p) for p in data.pop("sources")),
        criteria=tuple(Criterion(**c) for c in data.pop("criteria")),
        budget=Budget(**data.pop("budget")),
        permissions=Permissions(
            read_roots=tuple(resolve(p) for p in permissions["read_roots"]),
            output_root=resolve(permissions["output_root"]),
            allowed_models=tuple(permissions["allowed_models"]),
            send_documents=permissions.get("send_documents", False),
        ),
        control_file=resolve(control_file) if control_file else None,
        **data,
    )
