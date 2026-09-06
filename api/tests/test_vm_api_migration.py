import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PATCHER_PATH = ROOT / "infra/scripts/patch_caddy_for_vm_api.py"
SPEC = importlib.util.spec_from_file_location("patch_caddy_for_vm_api", PATCHER_PATH)
patcher = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(patcher)


def test_caddy_import_is_inserted_before_catch_all_and_is_idempotent():
    source = "example.test {\n\tencode gzip\n\n\thandle {\n\t\treverse_proxy 127.0.0.1:5000\n\t}\n}\n"
    path = "/etc/caddy/fpl-scout-api.caddy"
    rendered = patcher.render(source, path)
    assert rendered.index(f"import {path}") < rendered.index("handle {")
    assert patcher.render(rendered, path) == rendered


def test_caddy_import_rejects_an_ambiguous_config():
    with pytest.raises(ValueError, match="exactly one"):
        patcher.render("one.test { handle { } }\ntwo.test { handle { } }\n", "/tmp/api.caddy")


def test_vm_service_is_local_single_worker_and_resource_bounded():
    unit = (ROOT / "infra/deploy/gcp/systemd/fpl-scout-api.service").read_text()
    assert "--host 127.0.0.1 --port 8790 --workers 1" in unit
    assert "--limit-concurrency 16" in unit
    assert "EnvironmentFile=/etc/fpl-scout-api.env" in unit
    assert "MemoryMax=650M" in unit
    assert "WorkingDirectory=/opt/fpl-live-refresh/current/api" in unit
    assert "/opt/fpl-live-refresh/current/.venv/bin/python" in unit


def test_vm_installer_has_tag_match_privacy_health_and_rollback_gates():
    installer = (ROOT / "infra/deploy/install-vm-api.sh").read_text()
    for required in (
        "readlink -f \"$CURRENT\"",
        "FPL_DASHBOARD_READ_TOKEN",
        "caddy validate --adapter caddyfile",
        "127.0.0.1:8790/ready",
        "wait_for_url",
        "private-dashboard",
        "--rollback",
    ):
        assert required in installer


def test_scheduled_monitor_cannot_generate_recurring_load_traffic():
    task = (ROOT / "infra/scripts/run_scheduled_task.py").read_text()
    monitor_body = task[task.index("def task_monitor"):task.index("\ndef main", task.index("def task_monitor"))]
    assert '"--lightweight"' in monitor_body
    assert "load_smoke.py" not in monitor_body
    assert 'f"{SITE_URL}/league"' not in monitor_body

    workflow = (ROOT / ".github/workflows/scheduled-monitor.yml").read_text()
    assert 'cron: "17 */6 * * *"' in workflow
    assert 'cron: "7,37 * * * *"' not in workflow


def test_active_clients_do_not_fall_back_to_cloud_run():
    paths = [
        ROOT / "infra/scripts/run_scheduled_task.py",
        ROOT / "infra/scripts/monitor_production.py",
        ROOT / "infra/scripts/capture_predeadline_journal.py",
        *sorted((ROOT / "web/lib").glob("*.ts")),
    ]
    offenders = [str(path.relative_to(ROOT)) for path in paths if "run.app" in path.read_text()]
    assert offenders == []
