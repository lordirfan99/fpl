"""The deployment diagnostic must finish before plan writes or notifications."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pre_deadline_run as job


@pytest.mark.parametrize("valid_account", [True, False])
def test_verify_only_reads_account_and_context(monkeypatch, capsys, valid_account):
    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    bootstrap = {"events": [{"id": 3, "finished": False, "deadline_time": deadline}]}
    team = {"picks": [{"element": i, "selling_price": 50} for i in range(1, 16)],
            "transfers": {"bank": 0, "limit": 1, "made": 0}}
    if not valid_account:
        team["picks"].pop()
    monkeypatch.setattr(job.sys, "argv", ["pre_deadline_run.py", "--verify-inputs-only"])
    monkeypatch.setattr(job, "load_settings", lambda: {"team_id": 123})
    monkeypatch.setattr(job, "load_creds", lambda: {})
    monkeypatch.setattr(job, "load_player_prefs", lambda: {})
    monkeypatch.setattr(job, "fetch", lambda url: bootstrap if "bootstrap" in url else [])
    monkeypatch.setattr(job, "FPLClient", lambda: SimpleNamespace(my_team=lambda team_id: team))

    def context(league_id, gw, **kwargs):
        assert gw == 2 and kwargs["require_executable_plan"] is False
        return {"meta": {"freshness_hours": 0.1, "snapshot_gameweek": 2},
                "freshness": {"status": "provisional", "source": "official-fpl-live"}}

    def forbidden(*args, **kwargs):
        pytest.fail("Verification reached a plan mutation or optimization")

    monkeypatch.setattr(job, "fetch_competitive_v4", context)
    monkeypatch.setattr(job, "atomic_write_json", forbidden)
    monkeypatch.setattr(job, "optimize_horizon", forbidden)
    if valid_account:
        job.main()
    else:
        with pytest.raises(SystemExit) as error:
            job.main()
        assert error.value.code == 1
    result = capsys.readouterr().out
    assert '"plan_saved": false' in result
    assert '"card_sent": false' in result
    assert '"source": "authenticated_my_team"' in result
