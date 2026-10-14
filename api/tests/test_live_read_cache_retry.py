"""A failed public-reference seed can retry the same immutable tag."""
import os
import subprocess
from pathlib import Path


def test_seed_failure_restores_prior_release_and_clears_only_incomplete_state(tmp_path):
    repo, fakebin = tmp_path / "repo", tmp_path / "bin"
    repo.mkdir()
    fakebin.mkdir()
    runtime, backups, env_file = tmp_path / "runtime", tmp_path / "backups", tmp_path / "api.env"
    previous = runtime / "releases" / ("a" * 40)
    previous.mkdir(parents=True)
    (runtime / "current").symlink_to(previous)
    env_file.write_text(f"FPL_GIT_SHA={'a' * 40}\n")
    for name, body in {
        "sudo": 'if [ "$1" = -u ]; then shift 2; fi\nexec "$@"\n',
        "systemctl": '[ "$1" != is-active ]\n',
    }.items():
        path = fakebin / name
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)
    deploy = repo / "infra/deploy"
    deploy.mkdir(parents=True)
    (deploy / "install-live-refresh.sh").write_text('''#!/bin/bash
set -e
[ "$2" = --seed-references ]
# The staged installer's own failure cleanup is separately exercised.
[ "${FAIL_SEED:-0}" = 0 ] || exit 9
sha=$(git rev-parse HEAD)
mkdir -p "$TEST_RUNTIME/releases/$sha"
ln -sfn "$TEST_RUNTIME/releases/$sha" "$TEST_RUNTIME/current"
''')
    (deploy / "install-vm-api.sh").write_text("#!/bin/bash\nexit 0\n")
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()
    git("init")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    git("tag", "v-test")
    sha = git("rev-parse", "HEAD")
    source = Path(__file__).resolve().parents[2] / "infra/deploy/install-live-read-cache.sh"
    script = tmp_path / "installer.sh"
    script.write_text(source.read_text().replace("/opt/fpl-live-refresh", str(runtime))
                      .replace("/var/backups/fpl-live-read-cache", str(backups))
                      .replace("/etc/fpl-scout-api.env", str(env_file)))
    env = {**os.environ, "PATH": str(fakebin) + os.pathsep + os.environ["PATH"],
           "TEST_RUNTIME": str(runtime), "FAIL_SEED": "1"}
    command = ["bash", str(script), "v-test"]
    assert subprocess.run(command, cwd=repo, env=env).returncode != 0
    assert (runtime / "current").resolve() == previous
    assert not (backups / sha).exists()
    assert previous.is_dir()
    assert env_file.read_text() == f"FPL_GIT_SHA={'a' * 40}\n"
    env["FAIL_SEED"] = "0"
    assert subprocess.run(command, cwd=repo, env=env).returncode == 0
    assert (backups / sha / "complete").is_file()
    assert (runtime / "current").resolve() == runtime / "releases" / sha
