from importlib.resources import files
from pathlib import Path


def install_skills(agent: str, scope: str, directory: Path | None = None) -> dict:
    if scope == "user" and directory is not None:
        raise ValueError("--directory applies only to project scope")
    base = (Path.home() if scope == "user" else directory or Path.cwd()).resolve(
        strict=True
    )
    if not base.is_dir():
        raise ValueError("Choose an existing directory")
    folders = {
        "claude": ".claude/skills/spawn",
        "codex": ".agents/skills/spawn",
        "copilot": ".agents/skills/spawn",
    }
    targets = sorted(
        {
            base / folders[name] / "SKILL.md"
            for name in (folders if agent == "all" else [agent])
        }
    )
    content = files("spawn.templates").joinpath("SKILL.md").read_bytes()
    for target in targets:
        relative = target.relative_to(base)
        if any(
            (base / Path(*relative.parts[:i])).is_symlink()
            for i in range(1, len(relative.parts) + 1)
        ):
            raise ValueError("Refusing symlinked skill destinations")
        if target.exists() and target.read_bytes() != content:
            raise FileExistsError(f"Existing skill will not be overwritten: {target}")
    installed = []
    for target in targets:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(content)
            installed.append(str(target))
    return {
        "installed": installed,
        "available_at": [str(p) for p in targets],
        "scope": scope,
        "note": "Instructions only; no paid calls or permission settings changed. "
        "Reload the host's skills if needed. Automatic matching is host-dependent.",
    }
