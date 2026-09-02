"""Live Wildcard / Free Hit detection (drives the full-squad rebuild path)."""
import sys
import types

# pre_deadline_run imports the MILP solvers at module load; stub the heavy dep
# so this pure-function test stays fast and dependency-free.
sys.modules.setdefault("pulp", types.ModuleType("pulp"))

import importlib.util  # noqa: E402
import os  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "pdr_wc", os.path.join(os.path.dirname(__file__), "..", "jobs", "pre_deadline_run.py"))
pdr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pdr)


def test_detects_active_wildcard_for_target_gw():
    team = {"chips": [{"name": "wildcard", "status_for_entry": "active", "played_by_entry": [3]}]}
    assert pdr.detected_transfer_chip(team, 3) == "wildcard"
    assert pdr.detected_transfer_chip(team, 4) is None


def test_detects_pending_freehit():
    team = {"chips": [{"chip_type": "freehit", "status_for_entry": "ACTIVE",
                       "is_pending": True, "played_by_entry": []}]}
    assert pdr.detected_transfer_chip(team, 7) == "freehit"


def test_ignores_used_missing_and_non_transfer_chips():
    assert pdr.detected_transfer_chip({"chips": []}, 3) is None
    assert pdr.detected_transfer_chip({}, 3) is None
    assert pdr.detected_transfer_chip(
        {"chips": [{"name": "wildcard", "status_for_entry": "used", "played_by_entry": [1]}]}, 3) is None
    assert pdr.detected_transfer_chip(
        {"chips": [{"name": "bboost", "status_for_entry": "active", "played_by_entry": [3]}]}, 3) is None
