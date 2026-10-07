import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.journal import record_hash

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('journal_builder_retry', ROOT / 'infra/scripts/build_gameweek_journal.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_retry_preserves_frozen_record_and_rebuilds_exports(tmp_path, monkeypatch):
    source = ROOT / 'infra/data/journal/2026-27/gw01.json'
    record = json.loads(source.read_text())
    target = tmp_path / 'data/journal/2026-27/gw01.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(record))
    original = target.read_bytes()
    monkeypatch.setattr(MODULE, 'ROOT', tmp_path)
    monkeypatch.setattr(MODULE, 'config', lambda: {'active_season': '2026-27', 'primary_league_id': 58005, 'team_id': 2797967})
    monkeypatch.setattr(MODULE, 'live', lambda gw: pytest.fail('Frozen journal must not refetch outcomes'))
    monkeypatch.setattr(sys, 'argv', ['builder', '--gw', '1', '--reuse-existing'])
    assert MODULE.main() == 0
    assert target.read_bytes() == original
    assert (target.parent / 'exports/gameweeks.csv').is_file()
    assert json.loads((target.parent / 'index.json').read_text())['gameweeks'][0]['gameweek'] == 1


@pytest.mark.parametrize('corrupt', [True, False])
def test_retry_rejects_corrupt_or_wrong_identity(tmp_path, monkeypatch, corrupt):
    record = json.loads((ROOT / 'infra/data/journal/2026-27/gw01.json').read_text())
    record['gameweek'] = 2
    if not corrupt:
        record['record_hash'] = record_hash(record)
    target = tmp_path / 'data/journal/2026-27/gw02.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(record))
    monkeypatch.setattr(MODULE, 'ROOT', tmp_path)
    monkeypatch.setattr(MODULE, 'config', lambda: {'active_season': '2026-27', 'primary_league_id': 58005, 'team_id': 2797967})
    if corrupt:
        args = ['builder', '--gw', '2', '--reuse-existing']
        error = ValueError
    else:
        record['season'] = '2025-26'
        record['record_hash'] = record_hash(record)
        target.write_text(json.dumps(record))
        args = ['builder', '--gw', '2', '--reuse-existing']
        error = SystemExit
    monkeypatch.setattr(sys, 'argv', args)
    with pytest.raises(error):
        MODULE.main()
