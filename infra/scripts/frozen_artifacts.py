"""Verify or extend the append-only manifest for finalized season artifacts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "frozen_artifacts.json"


def digest(path: Path) -> str:
    # Git may check text files out with CRLF on Windows and LF in CI. Hash the
    # canonical repository representation so an unchanged JSON artifact has
    # one identity on every runner while content changes remain detectable.
    canonical = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def verify(manifest: dict) -> list[str]:
    failures = []
    for relative, expected in manifest.get("artifacts", {}).items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
        elif digest(path) != expected:
            failures.append(f"hash_mismatch:{relative}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--add", nargs="*", default=[])
    parser.add_argument("--correction-reason")
    args = parser.parse_args()
    manifest = load()
    if args.add:
        for raw in args.add:
            path = Path(raw).resolve()
            relative = path.relative_to(ROOT).as_posix()
            current = digest(path)
            previous = manifest["artifacts"].get(relative)
            if previous and previous != current and not args.correction_reason:
                raise SystemExit(f"refusing changed frozen artifact without --correction-reason: {relative}")
            if previous and previous != current:
                manifest.setdefault("corrections", []).append({
                    "path": relative, "previous_hash": previous, "replacement_hash": current,
                    "reason": args.correction_reason,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                })
            manifest["artifacts"][relative] = current
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    failures = verify(manifest)
    if failures:
        raise SystemExit("frozen artifact verification failed: " + ", ".join(failures))
    print(f"Verified {len(manifest['artifacts'])} frozen artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

