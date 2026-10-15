import pathlib
import subprocess
import unittest


class RecencyInstallerAllowlistTests(unittest.TestCase):
    def test_tagged_installer_deploys_and_import_checks_evaluator_runtime(self):
        path = pathlib.Path(__file__).parents[2] / "infra" / "deploy" / "install-recency-model.sh"
        script = path.read_text(encoding="utf-8")
        self.assertIn("model/evaluation_capture.py", script)
        self.assertIn("model/pipeline_health.py", script)
        self.assertIn("jobs/evaluate_frozen_forecasts.py", script)
        self.assertIn("import recent_form,v4_projection,v42_projection,evaluation_capture,evaluate_frozen_forecasts", script)
        self.assertIn('assert evaluation_capture.SCHEMA_VERSION == 1', script)

    def test_installer_remains_syntax_valid(self):
        installer = pathlib.Path(__file__).parents[2] / "infra" / "deploy" / "install-recency-model.sh"
        result = subprocess.run(["bash", "-n", str(installer)], check=False)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
