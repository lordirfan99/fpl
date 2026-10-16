import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


class RecencyInstallerAllowlistTests(unittest.TestCase):
    def test_tagged_installer_deploys_and_import_checks_evaluator_runtime(self):
        path = pathlib.Path(__file__).parents[2] / "infra" / "deploy" / "install-recency-model.sh"
        script = path.read_text(encoding="utf-8")
        self.assertIn("model/evaluation_capture.py", script)
        self.assertIn("model/pipeline_health.py", script)
        self.assertIn("jobs/evaluate_frozen_forecasts.py", script)
        self.assertIn("jobs/fpl_auto.py", script)
        self.assertIn("jobs/telegram_notify.py", script)
        self.assertIn("model/league_alerts.py", script)
        self.assertIn("import recent_form,v4_projection,v42_projection,evaluation_capture,evaluate_frozen_forecasts,fpl_auto", script)
        self.assertIn('assert evaluation_capture.SCHEMA_VERSION == 1', script)

    def test_allowlisted_installed_runner_imports_with_its_dependencies(self):
        root = pathlib.Path(__file__).parents[2]
        files = (
            "engine/jobs/fpl_auto.py", "engine/jobs/project_paths.py",
            "engine/jobs/telegram_notify.py", "engine/model/league_alerts.py",
            "engine/model/pipeline_health.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            runtime = pathlib.Path(temp) / "runtime"
            (runtime / "jobs").mkdir(parents=True)
            (runtime / "model").mkdir()
            (runtime / "config").mkdir()
            (runtime / "config/settings.json").write_text("{}", encoding="utf-8")
            for source in files:
                destination = runtime / source.removeprefix("engine/")
                shutil.copy2(root / source, destination)
            result = subprocess.run(
                ["python3", "-c", "import fpl_auto; assert fpl_auto.BASE"],
                cwd=runtime / "jobs", env={**os.environ,
                                            "FPL_AUTOPILOT_HOME": str(runtime)},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_installer_remains_syntax_valid(self):
        installer = pathlib.Path(__file__).parents[2] / "infra" / "deploy" / "install-recency-model.sh"
        result = subprocess.run(["bash", "-n", str(installer)], check=False)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
