"""Exercise install, rollback and failure recovery against redirected paths."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name == "nt", reason="Bash installer runs on Linux CI")
@pytest.mark.parametrize("fail_copy", [False, True])
def test_planner_install_is_scoped_and_recoverable(tmp_path, fail_copy):
    repo, runtime, fakebin = (tmp_path / name for name in ("repo", "runtime", "bin"))
    for path in (repo, runtime, fakebin):
        path.mkdir()
    files = ("model/competitive_v4_client.py", "jobs/pre_deadline_run.py")
    for file in files:
        src, dst = repo / "engine" / file, runtime / file
        src.parent.mkdir(parents=True, exist_ok=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("VERSION = 'new'\n")
        dst.write_text("VERSION = 'old'\n")
    python = runtime / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    for name, content in {
        "sudo": 'exec "$@"',
        "systemctl": '[ "$1" = is-active ] && exit "${TIMER_ACTIVE:-1}"; exit 99',
        "pgrep": 'exit 1',
        "install": 'if [ "${FAIL_COPY:-0}" = 1 ] && [[ "$*" == *engine/jobs/* ]]; then exit 9; fi\nexec /usr/bin/install "$@"',
    }.items():
        target = fakebin / name
        target.write_text("#!/bin/bash\n" + content + "\n")
        target.chmod(0o755)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    git("init")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    git("tag", "v-test")
    src = Path(__file__).resolve().parents[2] / "infra/deploy/install-recommendation-planner.sh"
    script = tmp_path / "install.sh"
    script.write_text(src.read_text().replace("/opt/fpl-autopilot", str(runtime))
                      .replace("/var/backups/fpl-planner", str(tmp_path / "backups"))
                      .replace("-o fpl -g fpl", ""))
    env = {**os.environ, "PATH": str(fakebin) + os.pathsep + os.environ["PATH"],
           "TIMER_ACTIVE": "0", "FAIL_COPY": str(int(fail_copy))}
    command = ["bash", str(script), "v-test"]
    assert subprocess.run(command, cwd=repo, env=env).returncode != 0
    assert all("old" in (runtime / f).read_text() for f in files)
    env["TIMER_ACTIVE"] = "1"
    result = subprocess.run(command, cwd=repo, env=env)
    assert (result.returncode != 0) == fail_copy
    expected = "old" if fail_copy else "new"
    assert all(expected in (runtime / f).read_text() for f in files)
    if not fail_copy:
        assert subprocess.run(command + ["--rollback"], cwd=repo, env=env).returncode == 0
        assert all("old" in (runtime / f).read_text() for f in files)
