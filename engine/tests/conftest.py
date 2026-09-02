"""Make the migrated engine importable + resolvable from repo root.

project_paths.resolve_project_root() identifies the project by
config/settings.json. On the VM that's a real file; here we seed a
non-sensitive one from the example if absent (it is .gitignored).
"""
import os
import pathlib

_ENGINE = pathlib.Path(__file__).resolve().parents[1]
_settings = _ENGINE / "config" / "settings.json"
if not _settings.exists():
    _settings.write_text((_ENGINE / "config" / "settings.example.json").read_text())
os.environ.setdefault("FPL_AUTOPILOT_HOME", str(_ENGINE))

collect_ignore_glob = ["deferred/*"]
