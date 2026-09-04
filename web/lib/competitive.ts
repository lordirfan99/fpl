import "server-only";
import type { Pick } from "./types";

const API_BASE = (process.env.FPL_API_BASE_URL ?? "https://fpl-scout-api-bztsnhv3ea-uc.a.run.app").replace(/\/$/, "");

export type CompetitivePhase = "CATCH" | "MATCH" | "ATTACK" | "CHASE";
export type CompetitiveRole = "ALIGN" | "CONTROLLED_EDGE" | "INVESTIGATE" | "AVOID" | "NEUTRAL";

export interface CompetitivePlayer extends Pick {
  xpts: number;
  form: number;
  pointsPerGame: number;
  eliteOwnership: number;
  eliteCaptaincy: number;
  fixture: string;
  fdr: number | null;
  risk: boolean;
  modelSupport: boolean;
  eliteCore: boolean;
  role: CompetitiveRole;
  score: number;
  count?: number;
  percentage?: number;
  starterPercentage?: number;
}

export type FreshnessStatus = "fresh" | "provisional" | "stale" | "safe_hold" | "needs_refresh";
// "Can I act on this?" — separate from data freshness (FreshnessStatus).
export type PacketStatus = "advisory" | "safe_hold" | "needs_refresh";

export interface RecommendationFreshness {
  source: string;
  status: FreshnessStatus;
  reason: string;
  stale: boolean;
  snapshotAt?: string;
  dataAgeHours?: number;
  bankKnown: boolean;
  accountStateVerified: boolean;
  accountGameweek?: number;
  rankProvenance?: string;
  missingFields: string[];
}

export interface CompetitiveRecommendation {
  gameweek: number;
  meta: { snapshotAt?: string; stale: boolean; freshnessHours?: number; qualityStatus: "valid" | "invalid" | "unknown"; qualityIssues: string[] };
  packetStatus: PacketStatus;
  freshness: RecommendationFreshness;
  eliteCount: number;
  eliteOverlap: number;
  eliteAverage: number;
  transfers: Array<{ incoming: CompetitivePlayer; outgoing: CompetitivePlayer; xptsGain: number; signalGain: number; gainBasis: string }>;
  captains: CompetitivePlayer[];
  risks: CompetitivePlayer[];
  missing: CompetitivePlayer[];
  competitive: {
    modelVersion: string;
    phase: CompetitivePhase;
    phaseReason: string;
    phaseInputs: { leaderGap: number; remainingGameweeks: number; chaseTrigger: number };
    alignment: number;
    targetAlignment: number;
    coreOwned: number;
    coreSize: number;
    criticalMissing: CompetitivePlayer[];
    modelEdges: CompetitivePlayer[];
    disagreements: CompetitivePlayer[];
    eliteTemplate: CompetitivePlayer[];
    templateFormation?: string;
    captainConsensus: CompetitivePlayer[];
    transferConsensus: Array<{ name: string; count: number; percentage: number }>;
    templateGate: { alignmentThreshold?: number; alignment?: number; differentialAllowed?: boolean; decision?: string };
    weights: { eliteConsensus: number; projection: number; currentSeasonEvidence: number };
    scoreDefinition: string;
    executionAuthority: "manual_fpl";
    writesEnabled: false;
  };
}

type Json = Record<string, unknown>;

export async function getCompetitiveRecommendation(leagueId: number, gameweek?: number): Promise<CompetitiveRecommendation> {
  // Omit `gw` so the API runs its freshness policy (fresh live snapshot first,
  // finalized fallback, honest safe_hold). Passing an explicit gw pins one
  // finalized snapshot and is only for historical lookups.
  const query = gameweek == null ? `league_id=${leagueId}` : `league_id=${leagueId}&gw=${gameweek}`;
  const response = await fetch(`${API_BASE}/v1/decision/current?${query}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Scout API returned ${response.status} for V4 competitive recommendation`);
  const raw = await response.json() as Json;
  const competitive = (raw.competitive as Json | undefined) ?? {};
  const meta = (raw.meta as Json | undefined) ?? {};
  const fresh = (raw.freshness as Json | undefined) ?? {};
  const weights = (competitive.weights as Json | undefined) ?? {};
  const phaseInputs = (competitive.phase_inputs as Json | undefined) ?? {};
  const players = (value: unknown) => ((value as Json[]) ?? []).map(player);
  return {
    gameweek: number(raw.gameweek),
    meta: {
      snapshotAt: text(meta.snapshot_at), stale: Boolean(meta.stale),
      freshnessHours: optionalNumber(meta.freshness_hours),
      qualityStatus: (text(meta.quality_status) ?? "unknown") as "valid" | "invalid" | "unknown",
      qualityIssues: (meta.quality_issues as string[]) ?? [],
    },
    packetStatus: (text(raw.packet_status) ?? "advisory") as PacketStatus,
    freshness: {
      source: text(fresh.source) ?? text(meta.data_source) ?? "unknown",
      status: (text(fresh.status) ?? text(meta.freshness_status) ?? (Boolean(meta.stale) ? "stale" : "fresh")) as FreshnessStatus,
      reason: text(fresh.reason) ?? text(meta.freshness_reason) ?? "",
      stale: Boolean(fresh.stale ?? meta.stale),
      snapshotAt: text(fresh.snapshot_at) ?? text(meta.snapshot_at),
      dataAgeHours: optionalNumber(fresh.data_age_hours ?? meta.data_age_hours ?? meta.freshness_hours),
      bankKnown: fresh.bank_known === true,
      accountStateVerified: fresh.account_state_verified === true,
      accountGameweek: optionalNumber(fresh.account_gameweek),
      rankProvenance: text(fresh.rank_provenance),
      missingFields: (fresh.missing_fields as string[]) ?? (meta.missing_fields as string[]) ?? [],
    },
    eliteCount: number(raw.elite_count), eliteOverlap: number(raw.elite_overlap),
    eliteAverage: number(raw.elite_average_points),
    transfers: ((raw.transfers as Json[]) ?? []).map((move) => ({
      incoming: player(move.incoming as Json), outgoing: player(move.outgoing as Json),
      xptsGain: number(move.xpts_gain), signalGain: number(move.signal_gain), gainBasis: text(move.gain_basis) ?? "unknown",
    })),
    captains: players(raw.captains), risks: players(raw.risks), missing: players(raw.missing_elite_players),
    competitive: {
      modelVersion: text(competitive.model_version) ?? "competitive-v4.0",
      phase: competitive.phase as CompetitivePhase, phaseReason: text(competitive.phase_reason) ?? "",
      phaseInputs: {
        leaderGap: number(phaseInputs.leader_gap), remainingGameweeks: number(phaseInputs.remaining_gameweeks),
        chaseTrigger: number(phaseInputs.chase_trigger),
      },
      alignment: number(competitive.alignment), targetAlignment: number(competitive.target_alignment),
      coreOwned: number(competitive.core_owned), coreSize: number(competitive.core_size),
      criticalMissing: players(competitive.critical_missing), modelEdges: players(competitive.model_edges),
      disagreements: players(competitive.disagreements),
      eliteTemplate: players(competitive.elite_template),
      templateFormation: text(competitive.template_formation),
      captainConsensus: players(competitive.captain_consensus),
      transferConsensus: ((competitive.transfer_consensus as Json[]) ?? []).map((row) => ({
        name: text(row.name) ?? "—", count: number(row.count), percentage: number(row.percentage),
      })),
      templateGate: {
        alignmentThreshold: optionalNumber((competitive.template_gate as Json | undefined)?.alignment_threshold),
        alignment: optionalNumber((competitive.template_gate as Json | undefined)?.alignment),
        differentialAllowed: Boolean((competitive.template_gate as Json | undefined)?.differential_allowed),
        decision: text((competitive.template_gate as Json | undefined)?.decision),
      },
      weights: {
        eliteConsensus: number(weights.elite_consensus), projection: number(weights.projection),
        currentSeasonEvidence: number(weights.current_season_evidence),
      },
      scoreDefinition: text(competitive.score_definition) ?? "",
      executionAuthority: "manual_fpl", writesEnabled: false,
    },
  };
}

function player(raw: Json): CompetitivePlayer {
  return {
    ...(raw as unknown as Pick), xpts: number(raw.xpts), form: number(raw.form),
    pointsPerGame: number(raw.points_per_game), eliteOwnership: number(raw.elite_ownership),
    eliteCaptaincy: number(raw.elite_captaincy), fixture: text(raw.fixture) ?? "Fixture TBC",
    fdr: raw.fdr == null ? null : number(raw.fdr), risk: Boolean(raw.risk),
    modelSupport: Boolean(raw.model_support), eliteCore: Boolean(raw.elite_core),
    role: raw.role as CompetitiveRole, score: number(raw.score),
    count: raw.count == null ? undefined : number(raw.count),
    percentage: raw.percentage == null ? undefined : number(raw.percentage),
    starterPercentage: raw.starter_percentage == null ? undefined : number(raw.starter_percentage),
  };
}

function number(value: unknown) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : 0; }
function optionalNumber(value: unknown) { return value == null ? undefined : number(value); }
function text(value: unknown) { return typeof value === "string" ? value : undefined; }
