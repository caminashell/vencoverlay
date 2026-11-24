"""Read-only checks of the project's version interface."""

import subprocess
import tomllib
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]


def test_version_matches_project_metadata() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        metadata = tomllib.load(stream)
    project: object = metadata["project"]
    assert isinstance(project, dict)
    version = cast(dict[object, object], project).get("version")
    assert isinstance(version, str)
    result = subprocess.run(
        ["bash", str(ROOT / "version.sh"), "show"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert result.stdout.strip() == version


def test_invalid_version_command_is_rejected() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "version.sh"), "invalid-command"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
