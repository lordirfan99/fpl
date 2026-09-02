"""Fail-closed validation for plans that can write to the FPL account."""
from collections import Counter
import datetime

SQUAD_QUOTA = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
LINEUP_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
LINEUP_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}


class InvalidPlanError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _player_ids(players, label, errors):
    ids = []
    for index, player in enumerate(players, 1):
        if not isinstance(player, dict) or player.get("id") is None:
            errors.append(f"{label} player {index} has no id")
        else:
            ids.append(player["id"])
    return ids


def validate_plan(plan, now=None, require_deadline=True):
    errors = []
    if not isinstance(plan, dict):
        return ["plan payload is not an object"]
    starters, bench = plan.get("target_starters") or [], plan.get("bench") or []
    if len(starters) != 11:
        errors.append(f"starting XI must contain 11 players (got {len(starters)})")
    if len(bench) != 4:
        errors.append(f"bench must contain 4 players (got {len(bench)})")
    starter_ids = _player_ids(starters, "starter", errors)
    bench_ids = _player_ids(bench, "bench", errors)
    all_ids = starter_ids + bench_ids
    if len(all_ids) != len(set(all_ids)):
        errors.append("starter and bench player ids must be unique and disjoint")
    xi_counts = Counter(p.get("position") for p in starters if isinstance(p, dict))
    squad_counts = Counter(p.get("position") for p in starters + bench if isinstance(p, dict))
    unknown = sorted(str(p) for p in squad_counts if p not in SQUAD_QUOTA)
    if unknown:
        errors.append(f"unknown player positions: {', '.join(unknown)}")
    if {p: squad_counts.get(p, 0) for p in SQUAD_QUOTA} != SQUAD_QUOTA:
        got = "/".join(str(squad_counts.get(p, 0)) for p in SQUAD_QUOTA)
        errors.append(f"squad quota must be 2/5/5/3 GKP/DEF/MID/FWD (got {got})")
    for pos in SQUAD_QUOTA:
        count = xi_counts.get(pos, 0)
        if count < LINEUP_MIN[pos] or count > LINEUP_MAX[pos]:
            errors.append(f"invalid XI {pos} count {count} (allowed {LINEUP_MIN[pos]}-{LINEUP_MAX[pos]})")
    clubs = []
    for player in starters + bench:
        if not isinstance(player, dict):
            continue
        if player.get("club") is None:
            errors.append(f"player {player.get('id', '?')} has no club")
        else:
            clubs.append(player["club"])
    club_counts = Counter(clubs)
    if club_counts and max(club_counts.values()) > 3:
        errors.append("squad contains more than 3 players from one club")
    captain_id = (plan.get("captain") or {}).get("id")
    vice_id = (plan.get("vice") or {}).get("id")
    if captain_id not in starter_ids:
        errors.append("captain must be in the starting XI")
    if vice_id not in starter_ids:
        errors.append("vice-captain must be in the starting XI")
    if captain_id is not None and captain_id == vice_id:
        errors.append("captain and vice-captain must be different players")
    transfers = plan.get("transfers") or []
    transfer_ins = [t.get("element_in") for t in transfers if isinstance(t, dict)]
    transfer_outs = [t.get("element_out") for t in transfers if isinstance(t, dict)]
    if any(x is None for x in transfer_ins + transfer_outs):
        errors.append("every transfer must have element_in and element_out")
    if len(transfer_ins) != len(set(transfer_ins)) or len(transfer_outs) != len(set(transfer_outs)):
        errors.append("transfer-in and transfer-out ids must be unique")
    if any(eid not in all_ids for eid in transfer_ins):
        errors.append("every transfer-in must appear in the target squad")
    if any(eid in all_ids for eid in transfer_outs):
        errors.append("a transfer-out still appears in the target squad")
    deadline = plan.get("deadline")
    if require_deadline and not deadline:
        errors.append("plan has no deadline")
    elif deadline:
        try:
            parsed = datetime.datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("naive deadline")
            if (now or datetime.datetime.now(datetime.timezone.utc)) >= parsed:
                errors.append("plan deadline has passed")
        except (TypeError, ValueError):
            errors.append("plan deadline is invalid")
    return errors


def expected_pre_transfer_ids(plan):
    target = {p["id"] for p in (plan.get("target_starters") or []) + (plan.get("bench") or [])
              if isinstance(p, dict) and p.get("id") is not None}
    transfers = plan.get("transfers") or []
    return (target - {t.get("element_in") for t in transfers}) | {t.get("element_out") for t in transfers}


def validate_live_squad(team, plan):
    actual = {p.get("element") for p in (team or {}).get("picks", [])}
    expected = expected_pre_transfer_ids(plan)
    if len(actual) != 15:
        return [f"live FPL squad must contain 15 players (got {len(actual)})"]
    if actual != expected:
        return ["live FPL squad changed after simulation; regenerate the plan"]
    return []
