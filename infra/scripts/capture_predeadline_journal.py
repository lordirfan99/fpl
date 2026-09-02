"""Capture immutable decision-time evidence locally and optionally in GCS."""
from __future__ import annotations

import argparse, hashlib, json, os, shutil, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
API = os.getenv("FPL_API_BASE_URL", "https://fpl-scout-api-bztsnhv3ea-uc.a.run.app").rstrip("/")

def active_season() -> str:
    return json.loads((ROOT / "data" / "journal" / "config.json").read_text(encoding="utf-8"))["active_season"]

def get_json(url: str) -> dict:
    with urlopen(Request(url, headers={"User-Agent": "FPLScoutJournal/1.0"}), timeout=60) as response:
        return json.load(response)

def canonical_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--season"); parser.add_argument("--gw", type=int); parser.add_argument("--require-cloud", action="store_true")
    parser.add_argument("--window-hours", type=float, default=6.0, help="Only capture this many hours before the deadline")
    args = parser.parse_args()
    args.season = args.season or active_season()
    bootstrap = get_json("https://fantasy.premierleague.com/api/bootstrap-static/")
    event = next((row for row in bootstrap["events"] if row.get("id") == args.gw), None) if args.gw else next((row for row in bootstrap["events"] if row.get("is_next")), None)
    if not event: raise SystemExit("No target gameweek")
    gw = int(event["id"]); deadline = datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    if now >= deadline: raise SystemExit(f"GW{gw} deadline has passed; refusing future-leaking capture")
    capture_opens = deadline - timedelta(hours=max(0.0, args.window_hours))
    if now < capture_opens:
        print(f"GW{gw} capture window opens at {capture_opens.isoformat()}; nothing to do")
        return 0
    decision = get_json(f"{API}/v1/decision/current?league_id=58005&gw={gw}")
    v5 = get_json(f"{API}/v1/projections/current?gw={max(1, gw-1)}")
    optimizer = get_json(
        f"{API}/v1/optimizer/transfers?league_id=58005&gw={max(1, gw-1)}&horizon=5&max_transfers=2"
    )
    health = get_json(f"{API}/health")
    fpl = [{"element": p["id"], "name": p.get("web_name"), "ep_next": p.get("ep_next")}
           for p in bootstrap.get("elements", [])]
    artifacts = {
        "decision": decision, "v5": v5, "fpl_baseline": fpl,
        "net_ev_optimizer": optimizer,
    }
    payload = {"schema_version": 2, "season": args.season, "gameweek": gw,
               "captured_at": datetime.now(timezone.utc).isoformat(), "deadline": event["deadline_time"],
               **artifacts,
               "artifact_hashes": {name: canonical_hash(value) for name, value in artifacts.items()},
               "source_provenance": {
                   "api_revision": health.get("revision"),
                   "api_version": health.get("version"),
                   "competitive_model": health.get("competitive_model"),
                   "v5_model": v5.get("projection_version"),
                   "optimizer_version": optimizer.get("optimizer_version"),
                   "execution_authority": health.get("execution_authority"),
               }}
    payload["input_hash"] = canonical_hash(payload)
    target = ROOT / "data" / "journal-raw" / args.season / f"gw{gw:02d}" / "predeadline.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        payload = json.loads(target.read_text(encoding="utf-8"))
        print(f"Immutable local capture already exists: {target}")
    else:
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    bucket_name = os.getenv("FPL_JOURNAL_BUCKET") or os.getenv("FPL_SNAPSHOT_BUCKET")
    if args.require_cloud and not bucket_name:
        raise SystemExit("FPL_JOURNAL_BUCKET is required for durable capture")
    if bucket_name:
        object_name = f"journal-raw/{args.season}/gw{gw:02d}/predeadline.json"
        try:
            from google.cloud import storage
            blob = storage.Client().bucket(bucket_name).blob(object_name)
            if blob.exists():
                print(f"Immutable GCS capture already exists for GW{gw}")
            else:
                blob.upload_from_string(json.dumps(payload, separators=(",", ":")), content_type="application/json", if_generation_match=0)
        except Exception:
            uri = f"gs://{bucket_name}/{object_name}"
            gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
            if not gcloud and os.name == "nt":
                candidate = Path(r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd")
                gcloud = str(candidate) if candidate.is_file() else None
            if not gcloud: raise RuntimeError("No authenticated GCS client or gcloud CLI is available")
            exists = subprocess.run([gcloud, "storage", "ls", uri], capture_output=True, text=True).returncode == 0
            if exists:
                print(f"Immutable GCS capture already exists for GW{gw}")
            else:
                subprocess.run([gcloud, "storage", "cp", str(target), uri, "--if-generation-match=0"], check=True)
    print(f"Captured GW{gw} pre-deadline journal evidence: {target}"); return 0

if __name__ == "__main__": sys.exit(main())
