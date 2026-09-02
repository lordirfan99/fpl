from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LeagueRegistry:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "league_registry.json"

    def read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        leagues = raw.get("leagues") if isinstance(raw, dict) else []
        if not isinstance(leagues, list):
            leagues = []
        clean = []
        seen: set[int] = set()
        for item in leagues:
            if not isinstance(item, dict):
                continue
            try:
                league_id = int(item["league_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if league_id <= 0 or league_id in seen:
                continue
            seen.add(league_id)
            clean.append({
                "league_id": league_id,
                "name": str(item.get("name") or item.get("official_name") or league_id),
                "official_name": item.get("official_name"),
                "status": str(item.get("status") or "active"),
                "tracking_mode": str(item.get("tracking_mode") or "full"),
                "latest_gameweek": item.get("latest_gameweek"),
                "last_refresh": item.get("last_refresh"),
                "error": item.get("error"),
            })
        return {
            "version": int(raw.get("version") or 1) if isinstance(raw, dict) else 1,
            "max_active": int(raw.get("max_active") or 10) if isinstance(raw, dict) else 10,
            "leagues": clean,
        }

    def active_ids(self) -> list[int]:
        return [item["league_id"] for item in self.read()["leagues"] if item["status"] == "active"]
