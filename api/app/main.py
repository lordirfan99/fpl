from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .league_registry import LeagueRegistry
from .live_freshness import snapshot_freshness
from . import live_fpl
from .recommendation_inputs import RecInputs, resolve_recommendation_inputs
from .recommendations import MODEL_VERSION, build_recommendations, cohort_summary, elite_managers
from .projection_types import PROJECTION_VERSION
from .projections import build_projections
from .multiweek_optimizer import MultiWeekContext, OPTIMIZER_VERSION, optimize_multiweek_transfers
from .repository import ArtifactIntegrityError, LiveSnapshotNotFoundError, SnapshotNotFoundError, SnapshotRepository
from .schemas import (
    ApiMeta,
    CatalogResponse,
    EliteResponse,
    LeagueResponse,
    LeagueSummaryResponse,
    Manager,
    ManagerSummary,
    ProjectionResponse,
    RecommendationResponse,
    TeamResponse,
)
from .settings import settings
from .validation import snapshot_quality


app = FastAPI(
    title="Fantasy Scout Intelligence API",
    version="4.0.0",
    description="Stable read API for the FPL dashboard and recommendation engine.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept", "Content-Type"],
)
repository = SnapshotRepository(settings.data_dir, settings.snapshot_bucket)
league_registry = LeagueRegistry(settings.data_dir)
SEASON_PATTERN = re.compile(r"^20\d{2}-\d{2}$")


@app.middleware("http")
async def structured_request_log(request: Request, call_next):
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    try:
        response = await call_next(request)
    except Exception as error:
        print(json.dumps({
            "level": "error", "message": "request_failed", "request_id": request_id,
            "method": request.method, "path": request.url.path,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "error_type": type(error).__name__,
        }), flush=True)
        raise
    response.headers["x-request-id"] = request_id
    response.headers["server-timing"] = f'app;dur={(time.perf_counter() - started) * 1000:.2f}'
    print(json.dumps({
        "level": "info", "message": "request_complete", "request_id": request_id,
        "method": request.method, "path": request.url.path, "status": response.status_code,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }), flush=True)
    return response


@app.get("/health")
def health() -> dict:
    readiness = _readiness()
    return {
        "status": "ok" if readiness["ready"] else "degraded", "service": "fpl-scout-api", "version": app.version,
        "revision": settings.git_revision, "build_time": settings.build_time,
        "competitive_model": MODEL_VERSION, "execution_authority": "manual_fpl",
        "dashboard_writes_enabled": False, "writes_enabled": False,
        "shared_snapshots": bool(settings.snapshot_bucket),
        "readiness": readiness,
    }


@app.get("/ready")
def ready() -> JSONResponse:
    payload = _readiness()
    return JSONResponse(payload, status_code=200 if payload["ready"] else 503)


@app.get("/v1/me")
def me() -> dict[str, int]:
    return {
        "team_id": settings.my_team_id,
        "default_league_id": settings.default_league_id,
        "current_gameweek": _current_gameweek(),
    }


@app.get("/v1/live/team")
def live_team(
    gw: int | None = Query(default=None, ge=1, le=38),
    league_id: int | None = Query(default=settings.default_league_id, gt=0),
) -> dict:
    """Return the current public FPL team state with a short server cache.

    Live data is deliberately separate from snapshot-backed endpoints. It is
    suitable for the dashboard's current-week display, but never for journal
    records or completed-gameweek analysis.
    """
    target = gw or live_fpl.current_gameweek()
    try:
        return live_fpl.team(settings.my_team_id, target, league_id)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Official FPL live data unavailable: {error}") from error


@app.get("/v1/me/team", response_model=TeamResponse)
def my_team(
    league_id: int = Query(default=settings.default_league_id, gt=0),
    gw: int | None = Query(default=None, ge=1, le=38),
) -> TeamResponse:
    if gw is None:
        live_gw = live_fpl.current_gameweek()
        # A live week may not have a finalized league artifact yet.
        gw = live_gw if live_gw == _current_gameweek() else _current_gameweek()
    snapshot = _league_or_404(league_id, gw)
    manager = next((item for item in snapshot.get("competitors", []) if item.get("entry_id") == settings.my_team_id), None)
    if manager is None:
        raise HTTPException(status_code=404, detail=f"Team {settings.my_team_id} is not in league {league_id} for GW{gw}")
    return TeamResponse(
        meta=_meta(snapshot),
        league_id=league_id,
        gameweek=gw,
        manager=manager,
        fixtures=repository.fixtures(min(gw + 1, 38)),
    )


@app.get("/v1/leagues/registry")
def league_registry_list() -> dict:
    """Return the shared tracked-league registry used by all clients."""
    registry = league_registry.read()
    return {
        "version": registry["version"],
        "max_active": registry["max_active"],
        "leagues": registry["leagues"],
    }


@app.get("/v1/leagues/{league_id}", response_model=LeagueResponse)
def league(league_id: int, gw: int | None = Query(default=None, ge=1, le=38)) -> LeagueResponse:
    gw = gw or _current_gameweek()
    snapshot = _league_or_404(league_id, gw)
    quality_status, quality_issues = snapshot_quality(snapshot)
    if quality_status != "valid":
        # Live/in-progress collector output may exist before it satisfies the
        # stable LeagueResponse contract. Do not let response-model validation
        # turn that expected provisional state into an opaque HTTP 500.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapshot_not_finalized",
                "league_id": league_id,
                "gameweek": gw,
                "quality_status": quality_status,
                "quality_issues": quality_issues[:10],
            },
        )
    raw_managers = sorted(snapshot.get("competitors", []), key=lambda item: item.get("league_rank") or 10**12)
    try:
        managers = [Manager.model_validate(item) for item in raw_managers]
    except ValidationError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapshot_not_finalized",
                "league_id": league_id,
                "gameweek": gw,
                "quality_status": "invalid",
                "quality_issues": [
                    f"schema:{'.'.join(str(part) for part in issue['loc'])}:{issue['type']}"
                    for issue in error.errors(include_input=False)[:10]
                ],
            },
        ) from error
    declared_count = int(snapshot.get("total_entries") or len(managers))
    return LeagueResponse(
        meta=_meta(snapshot), league_id=league_id, gameweek=gw, count=len(managers),
        declared_count=declared_count,
        hydration_percent=round(len(managers) / max(1, declared_count) * 100, 1),
        managers=managers,
    )


@app.get("/v1/leagues/{league_id}/summary", response_model=LeagueSummaryResponse)
def league_summary(
    league_id: int,
    gw: int | None = Query(default=None, ge=1, le=38),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    q: str = Query(default="", max_length=80),
) -> LeagueSummaryResponse:
    gw = gw or _current_gameweek()
    snapshot = _league_or_404(league_id, gw)
    quality_status, quality_issues = snapshot_quality(snapshot)
    if quality_status != "valid":
        raise HTTPException(status_code=409, detail={
            "code": "snapshot_not_finalized", "league_id": league_id, "gameweek": gw,
            "quality_status": quality_status, "quality_issues": quality_issues[:10],
        })
    try:
        summaries = [ManagerSummary.model_validate(row) for row in sorted(
            snapshot.get("competitors", []), key=lambda item: item.get("league_rank") or 10**12
        )]
    except ValidationError as error:
        raise HTTPException(status_code=409, detail={
            "code": "snapshot_not_finalized", "league_id": league_id, "gameweek": gw,
            "quality_status": "invalid", "quality_issues": [
                f"schema:{'.'.join(str(part) for part in issue['loc'])}:{issue['type']}"
                for issue in error.errors(include_input=False)[:10]
            ],
        }) from error
    term = q.strip().casefold()
    filtered = [row for row in summaries if not term or term in (
        f"{row.entry_name} {row.player_name} {row.entry_id} {row.league_rank}".casefold()
    )]
    pages = max(1, math.ceil(len(filtered) / page_size))
    if page > pages:
        page = pages
    start = (page - 1) * page_size
    mine = next((row for row in summaries if row.entry_id == settings.my_team_id), None)
    return LeagueSummaryResponse(
        meta=_meta(snapshot), league_id=league_id, gameweek=gw, total=len(summaries),
        filtered_total=len(filtered), page=page, page_size=page_size, pages=pages, query=q.strip(),
        average_gameweek_points=round(sum(row.gw_points for row in summaries) / max(1, len(summaries)), 1),
        leader=summaries[0] if summaries else None, manager=mine,
        managers=filtered[start:start + page_size],
    )


@app.get("/v1/leagues/{league_id}/directory")
def league_directory(
    league_id: int,
    gw: int | None = Query(default=None, ge=1, le=38),
) -> dict:
    """Compact manager picker data; intentionally excludes every squad."""
    gw = gw or _current_gameweek()
    snapshot = _league_or_404(league_id, gw)
    quality_status, quality_issues = snapshot_quality(snapshot)
    if quality_status != "valid":
        raise HTTPException(status_code=409, detail={
            "code": "snapshot_not_finalized", "quality_status": quality_status,
            "quality_issues": quality_issues[:10], "gameweek": gw,
        })
    try:
        managers = [ManagerSummary.model_validate(row).model_dump() for row in sorted(
            snapshot.get("competitors", []), key=lambda item: item.get("league_rank") or 10**12
        )]
    except ValidationError as error:
        raise HTTPException(status_code=409, detail={
            "code": "snapshot_not_finalized", "league_id": league_id, "gameweek": gw,
            "quality_status": "invalid", "quality_issues": [
                f"schema:{'.'.join(str(part) for part in issue['loc'])}:{issue['type']}"
                for issue in error.errors(include_input=False)[:10]
            ],
        }) from error
    return {"meta": _meta(snapshot).model_dump(mode="json"), "league_id": league_id,
            "gameweek": gw, "count": len(managers), "managers": managers}


@app.get("/v1/leagues/{league_id}/managers/{entry_id}", response_model=Manager)
def league_manager(
    league_id: int,
    entry_id: int,
    gw: int | None = Query(default=None, ge=1, le=38),
) -> Manager:
    gw = gw or _current_gameweek()
    snapshot = _league_or_404(league_id, gw)
    row = next((item for item in snapshot.get("competitors", []) if int(item.get("entry_id") or 0) == entry_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Manager {entry_id} is not in league {league_id} for GW{gw}")
    try:
        return Manager.model_validate(row)
    except ValidationError as error:
        raise HTTPException(status_code=409, detail={"code": "snapshot_not_finalized", "gameweek": gw}) from error


@app.get("/v1/catalog", response_model=CatalogResponse)
def catalog() -> CatalogResponse:
    bootstrap = repository.bootstrap()
    return CatalogResponse(
        meta=_bootstrap_meta(bootstrap),
        players=bootstrap.get("elements", []),
        teams=bootstrap.get("teams", []),
        events=bootstrap.get("events", []),
    )


@app.get("/v1/projections/current", response_model=ProjectionResponse)
def projections_current(
    gw: int | None = Query(default=None, ge=1, le=38),
) -> ProjectionResponse:
    """Return ownership-independent V5 laboratory projections.

    This endpoint is deliberately separate from production recommendations.
    Its candidate universe is the full official FPL catalogue, including
    players no tracked manager owns.
    """
    gw = gw or _current_gameweek()
    target_gw = min(gw + 1, 38)
    bootstrap = repository.bootstrap()
    rows = build_projections(bootstrap, repository.fixtures(target_gw))
    return ProjectionResponse(
        meta=_bootstrap_meta(
            bootstrap, source="projection-v5-lab", model_version=PROJECTION_VERSION,
            feature_version="v5-official-fpl-components-1",
        ),
        gameweek=target_gw,
        projection_version=PROJECTION_VERSION,
        players=[row.to_dict() for row in rows],
    )


@app.get("/v1/fixtures")
def fixtures(
    from_gw: int = Query(ge=1, le=38),
    to_gw: int = Query(ge=1, le=38),
) -> dict:
    if to_gw < from_gw or to_gw - from_gw > 9:
        raise HTTPException(status_code=400, detail="Fixture horizon must be between one and ten gameweeks")
    return {
        "from_gameweek": from_gw,
        "to_gameweek": to_gw,
        "gameweeks": repository.fixture_horizon(from_gw, to_gw),
    }


def _journal_season(value: str) -> str:
    if not SEASON_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="Season must use YYYY-YY format")
    return value


@app.get("/v1/journal")
def journal_index(season: str = Query(default="2026-27")) -> dict:
    season = _journal_season(season)
    try:
        return repository.journal_index(season)
    except SnapshotNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"No journal for season {season}") from error
    except ArtifactIntegrityError as error:
        raise HTTPException(status_code=409, detail={"code": "journal_integrity_failure", "message": str(error)}) from error


@app.get("/v1/journal/{season}/gw/{gameweek}")
def journal_gameweek(season: str, gameweek: int) -> dict:
    season = _journal_season(season)
    if not 1 <= gameweek <= 38:
        raise HTTPException(status_code=400, detail="Gameweek must be between 1 and 38")
    try:
        return repository.journal_gameweek(season, gameweek)
    except SnapshotNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"No {season} GW{gameweek} journal entry") from error
    except ArtifactIntegrityError as error:
        raise HTTPException(status_code=409, detail={"code": "journal_integrity_failure", "message": str(error)}) from error


@app.get("/v1/journal/{season}/export")
def journal_export(season: str, filename: str = Query(default="gameweeks.csv")) -> Response:
    season = _journal_season(season)
    if filename not in {"gameweeks.csv", "players.csv", "manifest.json", "README.md"}:
        raise HTTPException(status_code=400, detail="Unsupported journal export")
    try:
        body = repository.journal_export_bytes(season, filename)
    except SnapshotNotFoundError as error:
        raise HTTPException(status_code=404, detail="Journal export is not available") from error
    media_type = "text/csv" if filename.endswith(".csv") else "text/markdown" if filename.endswith(".md") else "application/json"
    return Response(
        content=body,
        media_type=media_type,
        headers={"content-disposition": f'attachment; filename="fpl-journal-{season}-{filename}"'},
    )


@app.get("/v1/elite/{gw}", response_model=EliteResponse)
def elite(
    gw: int,
    league_id: int = Query(default=settings.default_league_id, gt=0),
    percentile: int = Query(default=5, ge=1, le=25),
) -> EliteResponse:
    snapshot = _league_or_404(league_id, gw)
    cohort = elite_managers(
        snapshot.get("competitors", []),
        percentile,
        population_size=snapshot.get("population_size") or snapshot.get("total_entries"),
    )
    ownership, captaincy = cohort_summary(cohort)
    average = sum(item.get("gw_points", 0) for item in cohort) / max(1, len(cohort))
    return EliteResponse(
        meta=_meta(snapshot), league_id=league_id, gameweek=gw, percentile=percentile,
        count=len(cohort), average_points=round(average, 1), managers=cohort,
        ownership=ownership, captaincy=captaincy,
    )


REC_DISCLAIMER = "Decision support only. Verify late team news before approving any FPL action."


def _rec_meta(resolved: RecInputs) -> ApiMeta:
    """Build the recommendation meta from resolved freshness provenance."""
    snapshot_at = None
    if resolved.snapshot_at:
        try:
            snapshot_at = datetime.fromisoformat(str(resolved.snapshot_at).replace("Z", "+00:00"))
            if snapshot_at.tzinfo is None:
                snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
        except ValueError:
            snapshot_at = None
    # The catalogue (players/prices/fixtures) is always the freshest reference we
    # hold, so the packet is structurally "valid" even when the league context is
    # stale -- the honest signal for that is freshness_status / stale, not a
    # corruption verdict.
    quality = "valid" if resolved.usable else "unknown"
    return ApiMeta(
        source=resolved.source, snapshot_at=snapshot_at, stale=resolved.stale,
        freshness_hours=resolved.data_age_hours, snapshot_gameweek=resolved.gameweek,
        quality_status=quality,
        quality_issues=[] if resolved.usable else [resolved.reason],
        data_source=resolved.source, data_age_hours=resolved.data_age_hours,
        freshness_status=resolved.status, freshness_reason=resolved.reason,
        missing_fields=list(resolved.missing_fields),
        model_version=MODEL_VERSION, feature_version="competitive-features-v4",
        code_revision=settings.git_revision,
    )


def _hold_recommendation(league_id: int, resolved: RecInputs) -> RecommendationResponse:
    """An honest hold: no transfers, minimal competitive block, clearly stale."""
    return RecommendationResponse(
        meta=_rec_meta(resolved), league_id=league_id, gameweek=resolved.gameweek,
        team_id=settings.my_team_id,
        packet_status="safe_hold" if resolved.status == "safe_hold" else "needs_refresh",
        freshness=resolved.freshness(),
        elite_count=0, elite_overlap=0, elite_average_points=0.0,
        transfers=[], captains=[], risks=[], missing_elite_players=[],
        competitive={
            "model_version": MODEL_VERSION, "phase": "MATCH",
            "phase_reason": f"No fresh league context ({resolved.reason}); holding.",
            "alignment": 0, "target_alignment": 82,
            "execution_authority": "manual_fpl", "writes_enabled": False,
        },
        inputs={"bank_known": False},
        disclaimer=REC_DISCLAIMER,
    )


def _resolve_pinned(league_id: int, gw: int) -> RecInputs:
    """Explicit ?gw=N: that finalized snapshot only, labelled by its own age."""
    snapshot = _league_or_404(league_id, gw)
    managers = snapshot.get("competitors", [])
    manager = next((m for m in managers if m.get("entry_id") == settings.my_team_id), None)
    if manager is None:
        raise HTTPException(status_code=404, detail="Configured team is not present in this snapshot")
    meta = _meta(snapshot)
    age = meta.freshness_hours
    stale = bool(meta.stale)
    return RecInputs(
        bootstrap=repository.bootstrap(), fixtures=repository.fixtures(min(gw + 1, 38)),
        manager=manager, managers=managers, gameweek=gw,
        population_size=snapshot.get("population_size") or snapshot.get("total_entries"),
        source="finalized-snapshot",
        snapshot_at=meta.snapshot_at.isoformat() if meta.snapshot_at else None,
        data_age_hours=age, stale=stale,
        status="stale" if stale else "fresh",
        reason="explicit_gameweek_pin" + ("; stale" if stale else ""),
        rank_provenance="finalized-snapshot",
        bank_known=manager.get("gw_bank") is not None,
    )


@app.get("/v1/recommendations/current", response_model=RecommendationResponse)
def recommendations(
    league_id: int = Query(default=settings.default_league_id, gt=0),
    gw: int | None = Query(default=None, ge=1, le=38),
) -> RecommendationResponse:
    if gw is not None:
        resolved = _resolve_pinned(league_id, gw)
    else:
        resolved = resolve_recommendation_inputs(
            repository, league_id, settings.my_team_id, _current_gameweek()
        )

    if not resolved.usable:
        return _hold_recommendation(league_id, resolved)

    result = build_recommendations(
        resolved.manager, resolved.managers, resolved.bootstrap, resolved.fixtures,
        population_size=resolved.population_size, gameweek=resolved.gameweek, bank=None,
    )
    return RecommendationResponse(
        meta=_rec_meta(resolved), league_id=league_id, gameweek=resolved.gameweek,
        team_id=settings.my_team_id,
        packet_status="provisional" if resolved.status == "provisional" else "advisory",
        freshness=resolved.freshness(),
        disclaimer=REC_DISCLAIMER,
        **result,
    )


def _league_or_404(league_id: int, gw: int) -> dict:
    try:
        return repository.league(league_id, gw)
    except SnapshotNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"No snapshot for league {league_id}, GW{gw}") from error


def _meta(snapshot: dict, model_version: str | None = None, feature_version: str | None = None) -> ApiMeta:
    raw_snapshot_at = snapshot.get("fetched_at")
    snapshot_at = None
    if raw_snapshot_at:
        try:
            snapshot_at = datetime.fromisoformat(str(raw_snapshot_at).replace("Z", "+00:00"))
            if snapshot_at.tzinfo is None:
                snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
        except ValueError:
            snapshot_at = None
    freshness_hours = None
    if snapshot_at is not None:
        freshness_hours = round((datetime.now(timezone.utc) - snapshot_at).total_seconds() / 3600, 2)
    stale = freshness_hours is None or freshness_hours > 12
    quality_status, quality_issues = snapshot_quality(snapshot)
    return ApiMeta(
        run_id=snapshot.get("run_id"), source="snapshot", snapshot_at=snapshot_at,
        stale=stale, freshness_hours=freshness_hours,
        snapshot_gameweek=int(snapshot.get("gw")) if snapshot.get("gw") is not None else None,
        quality_status=quality_status, quality_issues=quality_issues,
        data_version=str(snapshot.get("run_id") or raw_snapshot_at or "") or None,
        data_hash=snapshot.get("content_sha256") or snapshot.get("_artifact_sha256"), cutoff_at=snapshot_at,
        feature_version=feature_version, model_version=model_version,
        code_revision=settings.git_revision,
    )


@app.get("/v1/optimizer/transfers")
def transfer_optimizer(
    league_id: int = Query(default=settings.default_league_id, gt=0),
    gw: int | None = Query(default=None, ge=1, le=38),
    horizon: int = Query(default=5, ge=2, le=6),
    max_transfers: int = Query(default=2, ge=1, le=3),
    free_transfers: int = Query(default=1, ge=0, le=5),
    chip: str | None = Query(default=None, max_length=20),
) -> dict:
    gw = gw or _current_gameweek()
    snapshot = _league_or_404(league_id, gw)
    manager = next((row for row in snapshot.get("competitors", []) if row.get("entry_id") == settings.my_team_id), None)
    if manager is None:
        raise HTTPException(status_code=404, detail="Configured team is not present in this snapshot")
    bootstrap = repository.bootstrap()
    first_target = min(gw + 1, 38)
    targets = list(range(first_target, min(38, first_target + horizon - 1) + 1))
    horizon_rows = [build_projections(bootstrap, repository.fixtures(target)) for target in targets]
    first_rows = horizon_rows[0]
    players = {row.element: row for row in first_rows}
    horizon_maps = [{row.element: row for row in rows} for rows in horizon_rows]
    prices = {int(row["id"]): float(row.get("now_cost") or 0) / 10 for row in bootstrap.get("elements", [])}
    eligible_player_ids = frozenset(
        int(row["id"]) for row in bootstrap.get("elements", [])
        if row.get("status", "a") == "a"
        and (row.get("chance_of_playing_next_round") is None
             or float(row.get("chance_of_playing_next_round") or 0) >= 75)
    )
    weights = tuple(round(0.85 ** index, 4) for index in range(len(targets)))
    if chip and chip.lower() not in {"wildcard", "freehit", "bboost", "3xc"}:
        raise HTTPException(status_code=400, detail="Unsupported chip mode")
    active_chip = chip or manager.get("active_chip")
    result = optimize_multiweek_transfers(
        [pick.get("element") for pick in manager.get("squad", [])], players, prices, horizon_maps,
        MultiWeekContext(
            bank=float(manager.get("gw_bank") or 0) / 10, free_transfers=free_transfers,
            weights=weights, max_transfers=max_transfers, active_chip=active_chip,
            eligible_player_ids=eligible_player_ids,
        ),
    )
    return {
        "meta": _bootstrap_meta(
            bootstrap, source="net-ev-optimizer", model_version=OPTIMIZER_VERSION,
            feature_version="v5-horizon-projections-1",
        ).model_dump(mode="json"),
        "league_id": league_id, "gameweek": gw, "target_gameweeks": targets,
        "free_transfers": free_transfers,
        "free_transfers_source": "request_or_default_assumption",
        "execution_authority": "manual_fpl", "writes_enabled": False,
        **result,
    }


@app.get("/v1/leagues/{league_id}/live")
def league_live(league_id: int) -> dict:
    """Return a complete, validated background-collected live snapshot."""
    try:
        snapshot = repository.live_league(league_id)
    except LiveSnapshotNotFoundError as error:
        raise HTTPException(status_code=503, detail="live_snapshot_unavailable") from error
    expected = int(snapshot["expected_count"])
    return {
        "meta": {
            "source": "official-fpl-live-snapshot",
            "snapshot_gameweek": snapshot["gameweek"],
            "quality_status": "complete",
            "generated_at": snapshot["captured_at"],
            "pages_fetched": snapshot.get("pages_fetched", 0),
            **snapshot_freshness(snapshot.get("captured_at")),
        },
        "league_id": league_id,
        "gameweek": snapshot["gameweek"],
        "count": expected,
        "declared_count": expected,
        "hydration_percent": 100.0,
        "managers": snapshot["managers"],
        "provisional": True,
    }


@app.get("/v1/leagues/{league_id}/live/status")
def league_live_status(league_id: int) -> dict:
    """Small monitoring response; never collect from FPL on a web request."""
    try:
        snapshot = repository.live_league(league_id)
    except LiveSnapshotNotFoundError as error:
        raise HTTPException(status_code=503, detail="live_snapshot_unavailable") from error
    freshness = snapshot_freshness(snapshot.get("captured_at"))
    return {
        "ready": not freshness["stale"], "league_id": league_id,
        "gameweek": snapshot["gameweek"], "captured_at": snapshot.get("captured_at"),
        "manager_count": snapshot["expected_count"], **freshness,
    }


@app.get("/v1/catalog/compact")
def compact_catalog() -> dict:
    bootstrap = repository.bootstrap()
    player_fields = ("id", "photo", "team", "event_points", "web_name", "element_type", "now_cost")
    team_fields = ("id", "name", "short_name", "code")
    event_fields = ("id", "name", "is_current", "is_next", "finished", "data_checked", "deadline_time")
    return {
        "meta": _bootstrap_meta(bootstrap).model_dump(mode="json"),
        "players": [{key: row.get(key) for key in player_fields} for row in bootstrap.get("elements", [])],
        "teams": [{key: row.get(key) for key in team_fields} for row in bootstrap.get("teams", [])],
        "events": [{key: row.get(key) for key in event_fields} for row in bootstrap.get("events", [])],
    }


def _bootstrap_meta(
    bootstrap: dict,
    source: str = "official-fpl-cache",
    model_version: str | None = None,
    feature_version: str | None = None,
) -> ApiMeta:
    raw = bootstrap.get("_meta") if isinstance(bootstrap.get("_meta"), dict) else {}
    snapshot_at = None
    if raw.get("fetched_at"):
        try:
            snapshot_at = datetime.fromisoformat(str(raw["fetched_at"]).replace("Z", "+00:00"))
        except ValueError:
            snapshot_at = None
    freshness = None if snapshot_at is None else round(
        (datetime.now(timezone.utc) - snapshot_at).total_seconds() / 3600, 2
    )
    issues = [] if snapshot_at is not None and raw.get("content_sha256") else ["bootstrap_provenance_missing"]
    return ApiMeta(
        source=source, snapshot_at=snapshot_at, freshness_hours=freshness,
        stale=freshness is None or freshness > 24,
        quality_status="valid" if not issues else "unknown", quality_issues=issues,
        data_version=str(raw.get("fetched_at") or "") or None,
        data_hash=raw.get("content_sha256"), cutoff_at=snapshot_at,
        feature_version=feature_version, model_version=model_version,
        code_revision=settings.git_revision,
    )


def _readiness() -> dict:
    checks: dict[str, dict[str, str | bool | int | float | None]] = {}
    try:
        bootstrap = repository.bootstrap()
        meta = _bootstrap_meta(bootstrap)
        checks["catalog"] = {
            "ok": meta.quality_status == "valid", "players": len(bootstrap.get("elements", [])),
            "snapshot_at": meta.snapshot_at.isoformat() if meta.snapshot_at else None,
            "freshness_hours": meta.freshness_hours,
        }
        fixture_horizon = repository.fixture_horizon(1, 38)
        populated = sum(1 for rows in fixture_horizon.values() if rows)
        checks["fixtures"] = {"ok": populated == 38, "populated_gameweeks": populated}
    except Exception as error:
        checks["catalog"] = {"ok": False, "error": type(error).__name__}
        checks["fixtures"] = {"ok": False, "error": type(error).__name__}
    try:
        index = repository.journal_index("2026-27")
        checks["journal"] = {"ok": True, "archived_gameweeks": len(index.get("gameweeks", []))}
    except Exception as error:
        checks["journal"] = {"ok": False, "error": type(error).__name__}
    ready_state = all(bool(check.get("ok")) for check in checks.values())
    return {
        "ready": ready_state, "revision": settings.git_revision,
        "checked_at": datetime.now(timezone.utc).isoformat(), "checks": checks,
    }


@app.get("/v1/decision/current")
def decision_current(
    league_id: int = Query(default=settings.default_league_id, gt=0),
    gw: int | None = Query(default=None, ge=1, le=38),
) -> dict:
    """Return the read-only decision-support packet for the current gameweek.

    The Scout system never writes to FPL. Every transfer, captain, lineup and
    chip change is reviewed and applied manually by the owner in the official
    FPL app. This packet is a locally derived V4 diagnostic built from the
    official catalogue, the latest finalized league snapshot and the projection
    model; it is advisory only and is never executable.
    """
    disclaimer = "Read-only decision support. Review and apply every change manually in the official FPL app."
    try:
        recommendation = recommendations(league_id=league_id, gw=gw).model_dump(mode="json")
    except HTTPException as error:
        if error.status_code != 404:
            raise
        # An explicit ?gw=N with no snapshot at all: expose the hold plainly
        # rather than inventing a move.
        pinned = gw or _current_gameweek()
        now = datetime.now(timezone.utc).isoformat()
        return {
            "decision_id": hashlib.sha256(f"{league_id}:{pinned}:safe_hold".encode()).hexdigest(),
            "model_version": MODEL_VERSION,
            "league_id": league_id,
            "gameweek": pinned,
            "generated_at": now,
            "meta": {"source": "snapshot", "stale": True, "quality_status": "unknown",
                     "quality_issues": ["missing_gameweek_snapshot"], "snapshot_gameweek": pinned,
                     "freshness_status": "needs_refresh", "freshness_reason": "missing_gameweek_snapshot"},
            "freshness": {"source": "none", "status": "needs_refresh",
                          "reason": "missing_gameweek_snapshot", "stale": True,
                          "snapshot_at": None, "data_age_hours": None},
            "competitive": {"model_version": MODEL_VERSION, "phase": "MATCH",
                             "phase_reason": "Waiting for the current gameweek snapshot.",
                             "alignment": 0, "target_alignment": 82},
            "plan": None,
            "packet_status": "safe_hold",
            "executable": False,
            "execution_authority": "manual_fpl",
            "writes_enabled": False,
            "disclaimer": disclaimer,
        }

    result_gw = recommendation["gameweek"]
    # The recommendation packet already carries its own honest status
    # (advisory / provisional / safe_hold / needs_refresh). Never upgrade it here.
    packet_status = recommendation.get("packet_status", "advisory")
    packet_body = {
        "league_id": league_id,
        "gameweek": result_gw,
        "competitive": recommendation["competitive"],
        "packet_status": packet_status,
    }
    decision_id = hashlib.sha256(json.dumps(packet_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        **recommendation,
        "decision_id": decision_id,
        "model_version": MODEL_VERSION,
        "league_id": league_id,
        "gameweek": result_gw,
        "generated_at": recommendation["meta"].get("generated_at"),
        "meta": recommendation["meta"],
        "freshness": recommendation.get("freshness", {}),
        "competitive": recommendation["competitive"],
        "plan": None,
        "packet_status": packet_status,
        "executable": False,
        "execution_authority": "manual_fpl",
        "writes_enabled": False,
        "disclaimer": disclaimer,
    }


def _current_gameweek() -> int:
    events = repository.bootstrap().get("events", [])
    current = next((event for event in events if event.get("is_current")), None)
    if current:
        candidate = int(current["id"])
    else:
        next_event = next((event for event in events if event.get("is_next")), None)
        if next_event:
            candidate = max(1, int(next_event["id"]) - 1)
        else:
            finished = [int(event["id"]) for event in events if event.get("finished")]
            candidate = max(finished, default=1)
    for gameweek in range(candidate, 0, -1):
        try:
            snapshot = repository.league(settings.default_league_id, gameweek)
        except SnapshotNotFoundError:
            continue
        if snapshot_quality(snapshot)[0] != "valid":
            continue
        try:
            managers = snapshot.get("competitors", [])
            if managers:
                for manager in managers:
                    Manager.model_validate(manager)
                return gameweek
        except ValidationError:
            continue
    return max(1, candidate)
