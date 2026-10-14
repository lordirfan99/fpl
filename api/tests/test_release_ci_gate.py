import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('ci_gate', Path(__file__).resolve().parents[2] / 'infra/scripts/check_release_ci.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def check(name, id, conclusion='success', status='completed'):
    return {'name': name, 'id': id, 'conclusion': conclusion, 'status': status}


@pytest.mark.parametrize('new_result', ['failure', 'cancelled', None])
def test_older_green_ci_cannot_authorize_a_release_after_a_new_failure(new_result):
    first, second = module.REQUIRED
    checks = [check(first, 1), check(second, 2), check(first, 3, new_result)]
    with pytest.raises(ValueError, match='Latest required CI'):
        module.require_latest_success(checks)


def test_newest_success_replaces_an_old_failure_and_missing_checks_fail_closed():
    first, second = module.REQUIRED
    module.require_latest_success([check(first, 1, 'failure'), check(first, 3), check(second, 2)])
    with pytest.raises(ValueError, match='Missing required CI'):
        module.require_latest_success([check(first, 3)])
