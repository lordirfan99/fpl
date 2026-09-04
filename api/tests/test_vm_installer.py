"""Exercise the release installer with all privileged paths redirected to tmp."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name == "nt", reason="Linux release installer; exercised in CI")
def test_failed_dependency_install_is_retryable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    fakebin = tmp_path / "bin"
    fakebin.mkdir()

    def executable(name, body):
        path = fakebin / name
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)

    executable("sudo", 'exec "$@"\n')
    executable("id", "exit 0\n")
    executable("systemctl", '[ "$1" != is-active ]\n')
    executable("python3", '''mkdir -p "$3/bin"
printf '#!/bin/bash\\nexit "${FAIL_PIP:-0}"\\n' > "$3/bin/pip"
printf '#!/bin/bash\\nexit 0\\n' > "$3/bin/python"
chmod +x "$3/bin/"*
''')
    for name in ("api/app/__init__.py", "api/requirements.txt", "infra/scripts/refresh_live_leagues.py",
                 "infra/deploy/gcp/systemd/fpl-live-refresh.service", "infra/deploy/gcp/systemd/fpl-live-refresh.timer"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    git("init")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    git("tag", "v-test")
    sha = git("rev-parse", "HEAD")
    runtime = tmp_path / "runtime"
    units = tmp_path / "units"
    units.mkdir()
    source = Path(__file__).resolve().parents[2] / "infra/deploy/install-live-refresh.sh"
    script = tmp_path / "install.sh"
    script.write_text(source.read_text().replace("/opt/fpl-live-refresh", str(runtime))
                      .replace("/etc/systemd/system/", str(units) + "/"))
    env = {**os.environ, "PATH": str(fakebin) + os.pathsep + os.environ["PATH"], "FAIL_PIP": "9"}
    command = ["bash", str(script), "v-test"]
    assert subprocess.run(command, cwd=repo, env=env).returncode != 0
    assert not (runtime / "releases" / sha).exists()
    env["FAIL_PIP"] = "0"
    assert subprocess.run(command, cwd=repo, env=env).returncode == 0
    assert (runtime / "current").resolve() == runtime / "releases" / sha
    assert subprocess.run(command, cwd=repo, env=env).returncode != 0
    assert (runtime / "releases" / sha).is_dir()
