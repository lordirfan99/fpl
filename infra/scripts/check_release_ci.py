"""Require the newest run of each required check, never an older green run."""
import json
import sys
from pathlib import Path

REQUIRED = ('engine / api / bot — lint + tests', 'web — build')


def require_latest_success(checks: list[dict]) -> None:
    for name in REQUIRED:
        candidates = [c for c in checks if c.get('name') == name]
        if not candidates:
            raise ValueError(f'Missing required CI: {name}')
        # GitHub check-run IDs increase on new suites and rerun attempts.
        latest = max(candidates, key=lambda c: int(c['id']))
        if latest.get('status') != 'completed' or latest.get('conclusion') != 'success':
            raise ValueError(f'Latest required CI is not green: {name}')


if __name__ == '__main__':
    require_latest_success(json.loads(Path(sys.argv[1]).read_text())['check_runs'])
