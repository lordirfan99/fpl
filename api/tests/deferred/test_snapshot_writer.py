from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import fetch_gw_data_fixed as writer  # noqa: E402


def test_final_snapshot_writer_refuses_implicit_replacement(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(writer, "DATA_DIR", str(tmp_path))
    competitors = [{"entry_id": 1, "entry_name": "A", "player_name": "B"}]
    writer.write_outputs(3, 58005, competitors, 0)
    with pytest.raises(FileExistsError):
        writer.write_outputs(3, 58005, competitors, 0)
    writer.write_outputs(3, 58005, competitors, 0, allow_correction=True)

