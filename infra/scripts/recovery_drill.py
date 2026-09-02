"""Non-destructive restore drill for frozen season artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main() -> int:
    manifest = json.loads((ROOT / "data" / "frozen_artifacts.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="fpl-recovery-drill-") as raw:
        restore = Path(raw)
        for relative, expected in manifest["artifacts"].items():
            source = ROOT / relative
            target = restore / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            assert digest(target) == expected, f"restored hash mismatch: {relative}"
            payload = json.loads(target.read_text(encoding="utf-8"))
            if "_league" in relative and relative.endswith("_data.json"):
                assert payload["total_entries"] == len(payload["competitors"]) > 0
            if "/journal/" in relative:
                record_hash = payload.pop("record_hash")
                canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                assert hashlib.sha256(canonical).hexdigest() == record_hash
        print(f"Recovery drill restored and verified {len(manifest['artifacts'])} frozen artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
