"""Backfill data-checked public event rows; never reads or changes an account."""
import argparse
import datetime
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model"))

from feature_store_v42 import event_rows, load_event_history, write_event_rows


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "FPL-history/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def backfill(path, bootstrap, fetch_live):
    """Skip complete existing GWs and atomically merge only final official rows."""
    existing = load_event_history(path)
    by_gw = {}
    for row in existing:
        by_gw.setdefault(int(row["gw"]), set()).add(int(row["element"]))
    # Revisit incomplete snapshots. Previously transferred/removed players are
    # not required, but every currently selectable player must have a row.
    expected = {int(player["id"]) for player in bootstrap["elements"]
                if player.get("can_select", True)}
    added = []
    for event in sorted(bootstrap.get("events", []), key=lambda row: row["id"]):
        gw = int(event["id"])
        if not event.get("finished") or not event.get("data_checked"):
            continue
        if expected and expected.issubset(by_gw.get(gw, set())):
            continue
        payload = fetch_live(gw)
        rows = event_rows(gw, payload, bootstrap["elements"],
                          datetime.datetime.now(datetime.timezone.utc).isoformat())
        if not rows:
            raise ValueError(f"Empty official event history for GW{gw}")
        write_event_rows(path, rows)
        added.append(gw)
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="Private/local JSONL feature store path; no account access required")
    args = parser.parse_args()
    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    added = backfill(str(args.output), bootstrap, lambda gw: fetch(
        f"https://fantasy.premierleague.com/api/event/{gw}/live/"))
    print(json.dumps({"data_checked_gameweeks_backfilled": added}))


if __name__ == "__main__":
    main()
