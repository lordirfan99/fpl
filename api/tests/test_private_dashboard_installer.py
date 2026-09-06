from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_private_dashboard_dependency_is_pinned_and_installed_before_preflight():
    requirement = (ROOT / "engine/requirements-private-dashboard.txt").read_text().strip()
    assert requirement == "google-cloud-storage==3.13.1"

    engine_requirements = (ROOT / "engine/requirements.txt").read_text()
    assert "-r requirements-private-dashboard.txt" in engine_requirements

    installer = (ROOT / "infra/deploy/install-private-dashboard.sh").read_text()
    install = installer.index("-r engine/requirements-private-dashboard.txt")
    preflight = installer.index("import google.cloud.storage")
    assert install < preflight
