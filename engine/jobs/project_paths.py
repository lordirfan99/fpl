"""Resolve the FPL project root for repo scripts and deployed cron copies."""
import os
from pathlib import Path


def venv_python(base=None):
    """Return this project's venv interpreter path for the CURRENT OS.

    Works on Windows (`.venv/Scripts/python.exe`) and POSIX/GCP
    (`.venv/bin/python`). `base` defaults to the project root resolved
    from `__file__` when called from this module.
    """
    root = Path(base) if base else Path(__file__).resolve().parents[1]
    if os.name == "nt":
        return str(root / ".venv" / "Scripts" / "python.exe")
    return str(root / ".venv" / "bin" / "python")


def _is_project(path):
    return (path / "config" / "settings.json").is_file()


def resolve_project_root(source_file, env=None, home=None):
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)

    override = env.get("FPL_AUTOPILOT_HOME")
    if override:
        candidate = Path(override).expanduser().resolve()
        if not _is_project(candidate):
            raise RuntimeError(f"FPL_AUTOPILOT_HOME is not a valid project: {candidate}")
        return candidate

    source_candidate = Path(source_file).resolve().parents[1]
    candidates = (source_candidate, home / "projects" / "fpl-autopilot")
    for candidate in candidates:
        if _is_project(candidate):
            return candidate

    raise RuntimeError(
        "FPL project root not found; set FPL_AUTOPILOT_HOME to the repository path"
    )
