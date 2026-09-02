"""
FPL Autopilot - fetch current-season match odds (football-data.co.uk).

Downloads E0 (EPL) CSV for the current season and validates it is REAL
odds data (not the HTML placeholder served pre-season). When real odds
first appear, prints a notification (delivered by no_agent cron) so the
v2 odds blend in pre_deadline_run.py activates.

Behavior (no_agent cron contract):
  - no changes / still placeholder  -> stdout EMPTY (silent)
  - odds now available (file changed from placeholder to real CSV) -> prints ALERT
  - real odds already present, unchanged -> silent (no spam on every run)
  - download/parse failure -> non-zero exit + stderr (cron alerts)

Schedule: every 6h. Source: https://www.football-data.co.uk/mmz4281/2627/E0.csv
"""
import os
import sys
import shutil
import tempfile
import urllib.request

from odds_feed import write_metadata

from project_paths import resolve_project_root

BASE = str(resolve_project_root(__file__))
ODDS_DIR = os.path.join(BASE, "data", "historical", "odds")
TARGET = os.path.join(ODDS_DIR, "E0_2026-27.csv")
URL = "https://www.football-data.co.uk/mmz4281/2627/E0.csv"
MIN_REAL_SIZE = 2000          # placeholder HTML is ~1.2KB; real CSV is 100KB+
REQUIRED_COLS = ["PSH", "PSD", "PSA"]   # Pinnacle 1X2


def looks_real(path):
    """True if file is a genuine odds CSV (has Pinnacle 1X2 columns)."""
    if not os.path.exists(path) or os.path.getsize(path) < MIN_REAL_SIZE:
        return False
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            header = f.readline()
        cols = [c.strip() for c in header.split(",")]
        return all(c in cols for c in REQUIRED_COLS)
    except Exception:
        return False


def main():
    os.makedirs(ODDS_DIR, exist_ok=True)
    was_real = looks_real(TARGET)

    # download to temp then atomically move (avoid partial writes)
    fd, tmp = tempfile.mkstemp(dir=ODDS_DIR, suffix=".tmp")
    os.close(fd)
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f)
        except urllib.error.HTTPError as he:
            os.remove(tmp)
            if he.code in {300, 404}:
                # Apache returns 300 when similarly named league files exist
                # but EPL E0 is not published yet; this is expected pre-season.
                # Silent (RC 0): not an error, just not ready.
                return 0
            sys.stderr.write(f"odds fetch HTTP {he.code}: {URL}\n")
            return 1
        if not looks_real(tmp):
            os.remove(tmp)
            if not was_real:
                return 0  # still placeholder - silent
            return 0      # real -> placeholder regression? keep silent, pipeline falls back
        # real odds in hand
        shutil.move(tmp, TARGET)
        write_metadata(TARGET, URL)
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        sys.stderr.write(f"odds fetch failed: {repr(e)[:200]}\n")
        return 1

    if not was_real:
        # transition: placeholder -> real odds. THIS is the notification.
        print(f"📈 FPL odds LIVE: E0_2026-27.csv available ({os.path.getsize(TARGET):,} bytes) "
              f"- v2 odds blend active in pre_deadline_run.py")
        print(f"   Source: {URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
