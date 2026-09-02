"""
FPL Autopilot - shared atomic file IO.

Every writer of shared JSON state (pending_plan.json, predictions, plan
snapshots, player prefs, reminder state) MUST use atomic_write_json:
write to a temp file in the same directory, then os.replace() into place.

os.replace is atomic on Windows for same-volume renames, so a concurrent
reader never sees a half-written file and a crash mid-write can't corrupt
state (P0.10 - previously only the bot wrote atomically; pre_deadline_run
wrote directly and could expose partial JSON to a concurrent reader).
"""

import json
import os
import tempfile


def atomic_write_json(path, obj, indent=1):
    """Atomically write obj as JSON to path. Raises on failure (tmp cleaned)."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=indent, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def atomic_write_text(path, text):
    """Atomically write a plain-text file (heartbeats, state signatures)."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
