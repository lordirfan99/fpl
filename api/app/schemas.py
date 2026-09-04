from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ApiMeta(BaseModel):
    schema_version: str = "api-meta-v2"
    run_id: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot_at: datetime | None = None
    source: str = "snapshot"
    stale: bool = False
    freshness_hours: float | None = None
    snapshot_gameweek: int | None = None
    quality_status: Literal["valid", "invalid", "unknown"] = "unknown"
    quality_issues: list[str] = Field(default_factory=list)
    # Recommendation-freshness provenance (see recommendation_inputs.RecInputs).
    # Optional so every other endpoint's meta is unchanged.
    data_source: str | None = None
    data_age_hours: float | None = None
    freshness_status: str | None = None
    freshness_reason: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    data_version: str | None = None
    data_hash: str | None = None
    cutoff_at: datetime | None = None
    feature_version: str | None = None
    model_version: str | None = None
    code_revision: str | None = None


class Pick(BaseModel):
    model_config = ConfigDict(extra="ignore")

    element: int
    name: str
    position: Literal["GKP", "DEF", "MID", "FWD"]
    team: str
    cost: float
    multiplier: int
    is_captain: bool
    is_vice_captain: bool
    selected_by: float | None = None


class Manager(BaseModel):
    model_config = ConfigDict(extra="allow")

    entry_id: int
    entry_name: str
    player_name: str
    gw_points: int
    total_points: int
    overall_rank: int = Field(validation_alias=AliasChoices("overall_rank", "rank"))
    league_rank: int
    squad_cost: float
    captain: str
    transfers_made: int = Field(validation_alias=AliasChoices("transfers_made", "gw_transfers"))
    squad: list[Pick]


class ManagerSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entry_id: int
    entry_name: str
    player_name: str
    gw_points: int
    total_points: int
    overall_rank: int = Field(validation_alias=AliasChoices("overall_rank", "rank"))
    league_rank: int
    squad_cost: float
    captain: str
    transfers_made: int = Field(validation_alias=AliasChoices("transfers_made", "gw_transfers"))


class TeamResponse(BaseModel):
    meta: ApiMeta
    league_id: int
    gameweek: int
    manager: Manager
    fixtures: list[dict[str, Any]]


class LeagueResponse(BaseModel):
    meta: ApiMeta
    league_id: int
    gameweek: int
    count: int
    declared_count: int
    hydration_percent: float
    managers: list[Manager]


class LeagueSummaryResponse(BaseModel):
    meta: ApiMeta
    league_id: int
    gameweek: int
    total: int
    filtered_total: int
    page: int
    page_size: int
    pages: int
    query: str
    average_gameweek_points: float
    leader: ManagerSummary | None
    manager: ManagerSummary | None
    managers: list[ManagerSummary]


class CatalogResponse(BaseModel):
    meta: ApiMeta
    players: list[dict[str, Any]]
    teams: list[dict[str, Any]]
    events: list[dict[str, Any]]


class ProjectionResponse(BaseModel):
    """Read-only V5 laboratory output; never an execution recommendation."""

    meta: ApiMeta
    gameweek: int
    projection_version: str
    players: list[dict[str, Any]]


class EliteResponse(BaseModel):
    meta: ApiMeta
    league_id: int
    gameweek: int
    percentile: int
    count: int
    average_points: float
    managers: list[Manager]
    ownership: list[dict[str, Any]]
    captaincy: list[dict[str, Any]]


class RecommendationResponse(BaseModel):
    meta: ApiMeta
    league_id: int
    gameweek: int
    team_id: int
    packet_status: Literal["advisory", "provisional", "safe_hold", "needs_refresh"] = "advisory"
    freshness: dict[str, Any] = Field(default_factory=dict)
    elite_count: int
    elite_overlap: int
    elite_average_points: float
    transfers: list[dict[str, Any]]
    captains: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    missing_elite_players: list[dict[str, Any]]
    competitive: dict[str, Any]
    inputs: dict[str, Any] = Field(default_factory=dict)
    disclaimer: str
