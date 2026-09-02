"""Tests for project-root discovery from repo and deployed cron copies."""
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "jobs"))

import project_paths  # noqa: E402


class TestProjectRootDiscovery(unittest.TestCase):
    @staticmethod
    def _project(path):
        (path / "config").mkdir(parents=True)
        (path / "config" / "settings.json").write_text("{}", encoding="utf-8")
        return path

    def test_resolves_script_running_inside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp) / "repo")
            script = root / "jobs" / "fetch_odds.py"
            script.parent.mkdir(exist_ok=True)
            script.touch()
            self.assertEqual(root, project_paths.resolve_project_root(script, env={}, home=Path(tmp)))

    def test_deployed_copy_falls_back_to_home_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "user"
            root = self._project(home / "projects" / "fpl-autopilot")
            script = home / "AppData" / "Local" / "hermes" / "scripts" / "fetch_odds.py"
            script.parent.mkdir(parents=True)
            script.touch()
            self.assertEqual(root, project_paths.resolve_project_root(script, env={}, home=home))

    def test_environment_override_supports_arbitrary_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp) / "elsewhere")
            script = Path(tmp) / "deployed" / "fpl_auto.py"
            script.parent.mkdir()
            script.touch()
            self.assertEqual(
                root,
                project_paths.resolve_project_root(
                    script, env={"FPL_AUTOPILOT_HOME": str(root)}, home=Path(tmp) / "user"),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
