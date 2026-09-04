"""Choose the freshest *safe* data bundle for a competitive recommendation.

`/v1/recommendations/current` historically read only the newest finalized league
snapshot. That file can be days old during a live gameweek, yet the endpoint
still returned transfer suggestions with no honest freshness signal. This module
implements the freshness policy:

1. Prefer the VM collector's complete live snapshot when it is fresh and carries
   every field the calculation needs.
2. If the live snapshot is missing an optional field, still use it but mark the
   result ``provisional`` (e.g. bank unknown -> affordability unconfirmed).
3. If no fresh league context exists and the finalized snapshot predates the
   live gameweek, return ``safe_hold`` -- never old transfers dressed as current.
4. Always report ``source``, ``snapshot_at``, ``data_age_hours``, ``stale`` and a
   machine-readable ``reason``.

The player catalogue, prices, fixtures and availability come from
``repository.bootstrap`` / ``repository.fixtures`` -- independent reference caches
that are already refreshed on their own cadence -- so they are never the stale
component here.

No FPL write path is imported or reachable from this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .live_freshness import snapshot_freshness
from .repository import LiveSnapshotNotFoundError, SnapshotNotFoundError

# Matches ``_meta`` in main.py so the finalized-path staleness call is consistent
# with the ``meta.stale`` a client already sees on other snapshot endpoints.
FINALIZED_MAX_AGE_HOURS = 12
# Anything further in the future than this is a clock/collector fault, not data.
FUTURE_SKEW_HOURS = 5 / 60

USABLE_STATUSES = ("fresh", "provisional", "stale")
HOLD_STATUSES = ("safe_hold", "needs_refresh")


@dataclass(frozen=True)
class RecInputs:
    """Everything ``build_recommendations`` needs, plus provenance."""

    bootstrap: dict[str, Any]
    fixtures: list[dict[str, Any]]
    manager: dict[str, Any] | None
    managers: list[dict[str, Any]]
    gameweek: int
    population_size: int | None
    source: str
    snapshot_at: str | None
    data_age_hours: float | None
    stale: bool
    status: str
    reason: str
    rank_provenance: str
    bank_known: bool
    missing_fields: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.status in USABLE_STATUSES

    def freshness(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "snapshot_at": self.snapshot_at,
            "data_age_hours": self.data_age_hours,
            "stale": self.stale,
            "status": self.status,
            "reason": self.reason,
            "missing_fields": list(self.missing_fields),
            "rank_provenance": self.rank_provenance,
            "bank_known": self.bank_known,
        }


def _age_hours(raw: Any, now: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return round((now - parsed).total_seconds() / 3600, 2)


def _bootstrap_current_gw(bootstrap: dict[str, Any]) -> int | None:
    for event in bootstrap.get("events", []):
        if event.get("is_current"):
            try:
                return int(event["id"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _hold(
    *, status: str, reason: str, gameweek: int, bootstrap: dict[str, Any], now: datetime
) -> RecInputs:
    return RecInputs(
        bootstrap=bootstrap, fixtures=[], manager=None, managers=[],
        gameweek=gameweek, population_size=None, source="none",
        snapshot_at=None, data_age_hours=None, stale=True, status=status,
        reason=reason, rank_provenance="none", bank_known=False,
    )


def _live_manager(managers: list[dict[str, Any]], team_id: int) -> dict[str, Any] | None:
    for manager in managers:
        try:
            if int(manager.get("entry_id") or 0) == team_id and len(manager.get("squad") or []) == 15:
                return manager
        except (TypeError, ValueError):
            continue
    return None


def resolve_recommendation_inputs(
    repo: Any,
    league_id: int,
    my_team_id: int,
    finalized_gw: int,
    *,
    now: datetime | None = None,
) -> RecInputs:
    """Resolve the freshest usable recommendation inputs for ``league_id``."""
    now = now or datetime.now(timezone.utc)

    # 1. Player/team catalogue -- independent of the league snapshot and always
    #    the freshest reference we have. Without it nothing can be computed.
    try:
        bootstrap = repo.bootstrap()
    except SnapshotNotFoundError:
        return _hold(
            status="needs_refresh", reason="catalogue_unavailable",
            gameweek=finalized_gw, bootstrap={}, now=now,
        )
    live_gw = _bootstrap_current_gw(bootstrap)

    # 2. Prefer the VM collector's complete live snapshot.
    live_reason = ""
    live: dict[str, Any] | None = None
    try:
        candidate = repo.live_league(league_id)
    except LiveSnapshotNotFoundError as error:
        candidate = None
        live_reason = f"live_snapshot_unavailable:{str(error)[:80]}"

    if candidate is not None:
        fresh = snapshot_freshness(candidate.get("captured_at"), now=now)
        if fresh["stale"]:
            live_reason = "live_snapshot_stale_or_unparsable"
        elif _live_manager(candidate.get("managers") or [], my_team_id) is None:
            live_reason = "team_absent_from_live_snapshot"
        elif live_gw is not None and int(candidate.get("gameweek") or 0) != live_gw:
            live_reason = "live_gameweek_mismatch"
        else:
            live = candidate

    if live is not None:
        managers = live["managers"]
        manager = _live_manager(managers, my_team_id)
        assert manager is not None  # guarded above
        bank_known = manager.get("gw_bank") is not None
        # `transfer_details` (the elite in/out player list) is a permanent,
        # known omission of the live collector -- it would cost one FPL call per
        # manager. `transfer_consensus` is simply empty in that case, which is
        # self-evident, so it is not counted as a per-snapshot gap here.
        missing: list[str] = [] if bank_known else ["gw_bank"]
        status = "provisional" if missing else "fresh"
        gameweek = int(live["gameweek"])
        return RecInputs(
            bootstrap=bootstrap,
            fixtures=repo.fixtures(min(gameweek + 1, 38)),
            manager=manager,
            managers=managers,
            gameweek=gameweek,
            population_size=live.get("expected_count") or len(managers),
            source="official-fpl-live",
            snapshot_at=live.get("captured_at"),
            data_age_hours=_age_hours(live.get("captured_at"), now),
            stale=False,
            status=status,
            reason="fresh_live_snapshot" if status == "fresh" else "live_snapshot_missing_optional_fields",
            rank_provenance=str(live.get("rank_provenance") or "unknown"),
            bank_known=bank_known,
            missing_fields=tuple(missing),
        )

    # 3. Fall back to the newest valid finalized snapshot.
    try:
        snap = repo.league(league_id, finalized_gw)
    except SnapshotNotFoundError:
        return _hold(
            status="needs_refresh",
            reason=_join("no_valid_league_snapshot", live_reason),
            gameweek=finalized_gw, bootstrap=bootstrap, now=now,
        )
    managers = snap.get("competitors") or []
    manager = next((m for m in managers if int(m.get("entry_id") or 0) == my_team_id), None)
    if manager is None or not managers:
        return _hold(
            status="needs_refresh",
            reason=_join("team_absent_from_finalized_snapshot", live_reason),
            gameweek=finalized_gw, bootstrap=bootstrap, now=now,
        )

    fetched_at = snap.get("fetched_at")
    age = _age_hours(fetched_at, now)
    future_skewed = age is not None and age < -FUTURE_SKEW_HOURS
    unparsable = age is None
    stale = unparsable or future_skewed or age > FINALIZED_MAX_AGE_HOURS
    predates_live = live_gw is not None and live_gw > finalized_gw

    if stale and predates_live:
        status = "safe_hold"
        base_reason = "no_fresh_source_and_snapshot_predates_live_gw"
    elif stale:
        status = "stale"
        base_reason = (
            "finalized_snapshot_timestamp_unparsable" if unparsable
            else "finalized_snapshot_timestamp_in_future" if future_skewed
            else f"finalized_snapshot_older_than_{FINALIZED_MAX_AGE_HOURS}h"
        )
    else:
        status = "fresh"
        base_reason = "fresh_finalized_snapshot"

    if status == "safe_hold":
        return RecInputs(
            bootstrap=bootstrap, fixtures=[], manager=manager, managers=managers,
            gameweek=finalized_gw,
            population_size=snap.get("population_size") or snap.get("total_entries"),
            source="finalized-snapshot", snapshot_at=fetched_at,
            data_age_hours=age, stale=True, status=status,
            reason=_join(base_reason, live_reason),
            rank_provenance="finalized-snapshot",
            bank_known=manager.get("gw_bank") is not None,
        )

    return RecInputs(
        bootstrap=bootstrap,
        fixtures=repo.fixtures(min(finalized_gw + 1, 38)),
        manager=manager,
        managers=managers,
        gameweek=finalized_gw,
        population_size=snap.get("population_size") or snap.get("total_entries"),
        source="finalized-snapshot",
        snapshot_at=fetched_at,
        data_age_hours=age,
        stale=stale,
        status=status,
        reason=_join(base_reason, live_reason),
        rank_provenance="finalized-snapshot",
        bank_known=manager.get("gw_bank") is not None,
    )


def _join(primary: str, secondary: str) -> str:
    return f"{primary}; {secondary}" if secondary else primary
