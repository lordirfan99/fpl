"""Lossless dashboard squad encoding, without collector debug/history extras."""
from typing import Any

from .schemas import ManagerSummary, Pick


def compact_managers(managers: list[dict[str, Any]]) -> dict:
    players: list[dict] = []
    lookup: dict[tuple, int] = {}
    rows = []
    seen = set()
    for raw in managers:
        row = ManagerSummary.model_validate(raw).model_dump()
        if row["entry_id"] in seen:
            raise ValueError("Duplicate manager in snapshot")
        seen.add(row["entry_id"])
        if len(raw.get("squad") or []) != 15:
            raise ValueError("Incomplete manager squad")
        squad = []
        for raw_pick in raw["squad"]:
            pick = Pick.model_validate(raw_pick).model_dump()
            flags = [pick.pop("multiplier"), pick.pop("is_captain"), pick.pop("is_vice_captain")]
            # Include captured price/ownership in identity, never rebuild from
            # today's catalog. Different prices for the same player stay distinct.
            key = tuple(pick.items())
            index = lookup.get(key)
            if index is None:
                index = len(players)
                lookup[key] = index
                players.append(pick)
            squad.append([index, *flags])
        row["squad"] = squad
        if raw.get("league_last_rank") is not None:
            row["league_last_rank"] = int(raw["league_last_rank"])
        # Dashboard research only needs names; do not ship upstream history,
        # detailed FPL response objects or unrelated collector fields.
        for field, keys in (("transfer_details", ("out", "in")), ("chips_used", ("name", "chip_name"))):
            if isinstance(raw.get(field), list):
                row[field] = [{k: item[k] for k in keys if k in item} for item in raw[field] if isinstance(item, dict)]
        rows.append(row)
    return {"encoding": "league-squads-v1", "players": players, "managers": rows}
