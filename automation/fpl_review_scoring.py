"""Pure, fail-closed scoring for frozen FPL hybrid-review snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, NoReturn, cast
from urllib.parse import urlparse


class ScoringError(ValueError):
    """Raised when a recommendation or frozen outcome is not scoreable."""


_POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
_HIT_COST_MAX = 60


def _error(message: str) -> NoReturn:
    raise ScoringError(message)


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _error(f"{label} must be an integer")
    return value


def _player_id(pick: Mapping[str, Any]) -> int:
    if "element" in pick:
        return _int(pick["element"], "pick element")
    if "id" in pick:
        return _int(pick["id"], "pick id")
    _error("pick has no player id")


def _metadata(player_metadata: Mapping[Any, Mapping[str, Any]], pid: int) -> Mapping[str, Any]:
    row = player_metadata.get(pid)
    if row is None:
        row = player_metadata.get(str(pid))
    if not isinstance(row, Mapping):
        _error(f"missing metadata for player {pid}")
    return cast(Mapping[str, Any], row)


def _validate_hit_cost(recommendation: Mapping[str, Any]) -> int:
    value = recommendation.get("hit_cost", 0)
    value = _int(value, "hit_cost")
    if value < 0 or value % 4 or value > _HIT_COST_MAX:
        _error("hit_cost must be a non-negative multiple of 4 no greater than 60")
    for key in ("chip", "chips", "active_chip"):
        if recommendation.get(key) is not None:
            _error("chips are unsupported by the scorer")
    return value


def validate_picks(
    picks: Any, player_metadata: Mapping[Any, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Validate a legal 15-man squad and return shallow-copied picks.

    Metadata is deliberately required for every selected player.  This prevents
    a missing official row from silently changing either squad legality or score.
    """
    if not isinstance(picks, list) or len(picks) != 15:
        _error("picks must contain exactly 15 players")
    if not isinstance(player_metadata, Mapping):
        _error("player metadata must be a mapping")
    result: list[dict[str, Any]] = []
    ids: set[int] = set()
    positions: set[int] = set()
    counts = {position: 0 for position in _POSITIONS}
    clubs: dict[int, int] = {}
    captain = vice = 0
    for pick in picks:
        if not isinstance(pick, Mapping):
            _error("each pick must be an object")
        copied = dict(pick)
        pid = _player_id(copied)
        slot = _int(copied.get("position"), "pick position")
        if slot not in range(1, 16) or slot in positions:
            _error("pick positions must be unique integers 1..15")
        if pid in ids:
            _error("duplicate player id")
        ids.add(pid)
        positions.add(slot)
        row = _metadata(player_metadata, pid)
        player_position = _int(row.get("position"), f"position for player {pid}")
        if player_position not in _POSITIONS:
            _error(f"invalid position for player {pid}")
        counts[player_position] += 1
        club = row.get("club")
        if club is None:
            _error(f"missing club for player {pid}")
        club_id = _int(club, f"club for player {pid}")
        clubs[club_id] = clubs.get(club_id, 0) + 1
        for flag in ("is_captain", "is_vice_captain"):
            if flag in copied and not isinstance(copied[flag], bool):
                _error(f"{flag} must be boolean")
        if copied.get("is_captain"):
            captain += 1
        if copied.get("is_vice_captain"):
            vice += 1
        result.append(copied)
    if counts != {1: 2, 2: 5, 3: 5, 4: 3}:
        _error("squad must contain 2 GK, 5 DEF, 5 MID and 3 FWD")
    if max(clubs.values()) > 3:
        _error("squad may contain at most three players per club")
    if captain != 1 or vice != 1:
        _error("exactly one captain and one vice-captain are required")
    captain_id = next(_player_id(p) for p in result if p.get("is_captain"))
    vice_id = next(_player_id(p) for p in result if p.get("is_vice_captain"))
    if captain_id == vice_id:
        _error("captain and vice-captain must be distinct")
    if any(p.get("position", 0) > 11 for p in result if p.get("is_captain") or p.get("is_vice_captain")):
        _error("captain and vice-captain must both be in the starting XI")
    if any(p.get("position") == 12 and _metadata(player_metadata, _player_id(p)).get("position") != 1 for p in result):
        _error("position 12 must be the bench goalkeeper")
    if any(p.get("position") in (13, 14, 15) and _metadata(player_metadata, _player_id(p)).get("position") == 1 for p in result):
        _error("positions 13..15 must be outfield bench players")
    if not _legal_xi([p for p in result if p['position'] <= 11], player_metadata):
        _error("starting XI does not satisfy legal formation")
    return result


def _outcome(players: Mapping[Any, Mapping[str, Any]], pid: int) -> Mapping[str, Any]:
    row = _metadata(players, pid)
    minutes = _int(row.get("minutes"), f"minutes for player {pid}")
    points = _int(row.get("points"), f"points for player {pid}")
    if minutes < 0:
        _error(f"negative minutes for player {pid}")
    return row


def _legal_xi(active: list[dict[str, Any]], players: Mapping[Any, Mapping[str, Any]]) -> bool:
    counts = {position: 0 for position in _POSITIONS}
    for pick in active:
        position = _int(_metadata(players, _player_id(pick)).get("position"), "player position")
        counts[position] += 1
    return (
        1 <= counts[1] <= 1
        and 3 <= counts[2] <= 5
        and 2 <= counts[3] <= 5
        and 1 <= counts[4] <= 3
    )


def score_lineup(recommendation: Mapping[str, Any], players: Mapping[Any, Mapping[str, Any]]) -> int:
    """Return gross points after deterministic, legal autosubs and captaincy."""
    if not isinstance(recommendation, Mapping):
        _error("recommendation must be an object")
    if not isinstance(players, Mapping):
        _error("players must be a mapping")
    _validate_hit_cost(recommendation)
    picks = recommendation.get("picks")
    if picks is None:
        picks = recommendation.get("lineup")
    picks = validate_picks(picks, players)
    for pick in picks:
        _outcome(players, _player_id(pick))

    starters = sorted((p for p in picks if p["position"] <= 11), key=lambda p: p["position"])
    bench = sorted((p for p in picks if p["position"] >= 12), key=lambda p: p["position"])
    if not _legal_xi(starters, players):
        _error("starting XI does not satisfy legal formation")
    active = list(starters)
    unused = [p for p in bench if p["position"] != 12]
    starting_gk = next(p for p in starters if _metadata(players, _player_id(p))["position"] == 1)
    if _outcome(players, _player_id(starting_gk))["minutes"] == 0:
        bench_gk = next(p for p in bench if p["position"] == 12)
        if _outcome(players, _player_id(bench_gk))["minutes"] > 0:
            candidate = [p for p in active if p is not starting_gk] + [bench_gk]
            if _legal_xi(candidate, players):
                active[active.index(starting_gk)] = bench_gk

    # Bench order is authoritative.  Each row is considered once and can enter
    # only one DNP starter; there is no points-based hindsight selection.
    for substitute in unused:
        if _outcome(players, _player_id(substitute))["minutes"] == 0:
            continue
        for starter in list(active):
            if starter not in starters or _outcome(players, _player_id(starter))["minutes"] != 0:
                continue
            candidate = list(active)
            candidate[candidate.index(starter)] = substitute
            if _legal_xi(candidate, players):
                active = candidate
                break

    captain = next(p for p in picks if p.get("is_captain"))
    vice = next(p for p in picks if p.get("is_vice_captain"))
    captain_id = _player_id(captain)
    vice_id = _player_id(vice)
    captain_played = _outcome(players, captain_id)["minutes"] > 0
    total = sum(_outcome(players, _player_id(p))["points"] for p in active)
    if captain_played:
        total += _outcome(players, captain_id)["points"]
    elif _outcome(players, vice_id)["minutes"] > 0:
        total += _outcome(players, vice_id)["points"]
    return total


def _parse_deadline(value: Any) -> datetime:
    if not isinstance(value, str):
        _error("deadline must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScoringError("deadline must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _error("deadline must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def evaluate(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate model and hybrid recommendations against frozen official rows."""
    if not isinstance(payload, Mapping):
        _error("payload must be an object")
    if payload.get("status") != "finished" or payload.get("data_checked") is not True:
        _error("evaluation requires finished, data_checked outcome")
    gameweek = _int(payload.get("gameweek"), "gameweek")
    if gameweek not in range(1, 39):
        _error("gameweek must be between 1 and 38")
    if payload.get("deadline") is not None and _parse_deadline(payload["deadline"]) > datetime.now(timezone.utc):
        _error("evaluation deadline is still in the future")
    players = payload.get("players")
    if not isinstance(players, Mapping):
        _error("players are required")
    result: dict[str, Any] = {
        "gameweek": gameweek,
        "evaluated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "scope": "weekly_decision_comparison",
    }
    for name in ("model", "hybrid"):
        recommendation = payload.get(name)
        if not isinstance(recommendation, Mapping):
            _error(f"missing {name} recommendation")
        gross = score_lineup(recommendation, players)
        hit_cost = _validate_hit_cost(recommendation)
        result[name] = {"gross_points": gross, "hit_cost": hit_cost, "net_points": gross - hit_cost}
    actual = payload.get("owner_actual")
    if not isinstance(actual, Mapping) or actual.get("official") is not True:
        _error("owner_actual must be explicitly official")
    net_points = _int(actual.get("net_points"), "owner actual net_points")
    source_url = actual.get("source_url", actual.get("sourceURL", actual.get("source")))
    if not isinstance(source_url, str) or not source_url:
        _error("owner_actual requires source_url")
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.hostname not in {"fantasy.premierleague.com", "www.fantasy.premierleague.com"}:
        _error("owner_actual source_url must be an official Fantasy Premier League URL")
    result["owner_actual"] = {"net_points": net_points, "source": source_url, "source_url": source_url}
    return result
