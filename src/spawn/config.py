import json
import tomllib
from pathlib import Path

from .contracts import Budget, Criterion, Model, Permissions, Request


def load_models(path: Path) -> list[Model]:
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    return [Model(**entry) for entry in data["models"]]


def load_request(path: Path) -> Request:
    return request_from_data(
        json.loads(path.read_text(encoding="utf-8")), path.resolve().parent
    )


def request_from_data(data: dict, base: Path) -> Request:
    data = dict(data)

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


def configuration_from_data(data: dict, base: Path) -> tuple[Request, list[Model]]:
    if not isinstance(data, dict) or set(data) - {
        "request",
        "models",
        "copilot_models",
    }:
        raise ValueError("Use a JSON object with request and models or copilot_models")
    if ("models" in data) == ("copilot_models" in data):
        raise ValueError("Supply exactly one of models or copilot_models")
    request = request_from_data(data["request"], base)
    if "models" in data:
        entries = data["models"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("models must be a nonempty list of model profiles")
        models = [Model(**entry) for entry in entries]
    else:
        from .profiles import copilot_profiles

        ids = data["copilot_models"]
        if ids is None:
            raise ValueError("copilot_models must be a nonempty list of model IDs")
        models = copilot_profiles(ids)
    return request, models
