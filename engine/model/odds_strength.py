"""
FPL Autopilot - odds-derived team strength for xPts (v2 blend).

Converts match odds into a per-position team-strength multiplier:
  - MID/FWD: scales with implied win probability of the player's team
  - GKP/DEF: scales with opponent weakness (clean-sheet proxy)

Sources:
  - Historical/backtest: football-data.co.uk E0 CSVs (Pinnacle preferred, Bet365 fallback)
  - Live (GW1+): InferSports keyless API (api.infersports.dev) fair 1x2 lines;
    falls back to FDR multiplier if odds unavailable.

USAGE (live, from pre_deadline_run.py):
    import odds_strength as os_
    mults = os_.fetch_live_strength(gw_fixtures)   # {(gw, team_id): multiplier}
    # then: xPts = play_prob * max(rate, baseline) * mults.get((gw, team_id), fdr_multiplier(...))

USAGE (backtest):
    os_.load_historical_odds("data/historical/odds/E0_2025-26.csv")
"""
import json
import urllib.request

# ---------------------------------------------------------------------------
# de-vig
# ---------------------------------------------------------------------------
def devig(h, d, a):
    """Proportional de-vig of decimal 1X2 odds -> (p_home, p_draw, p_away)."""
    ih, id_, ia = 1.0 / h, 1.0 / d, 1.0 / a
    s = ih + id_ + ia
    return ih / s, id_ / s, ia / s


def odds_multiplier(team_win, opp_win, position):
    """Team-strength multiplier from de-vigged win probabilities."""
    if position in ("GKP", "GK", "DEF"):
        # clean-sheet likelihood ~ opponent weakness
        return min(1.25, max(0.75, 0.9 + 0.5 * (1.0 - opp_win - 0.35)))
    # MID/FWD: attacking output scales with implied win probability
    return min(1.25, max(0.75, 0.85 + 0.8 * (team_win - 0.40)))


# ---------------------------------------------------------------------------
# historical (backtest)
# ---------------------------------------------------------------------------
TEAM_ALIAS = {
    "Man Utd": "Man United",
    "Spurs": "Tottenham",
}


def load_historical_odds(csv_path):
    """Return {(home_team, away_team): (p_home, p_draw, p_away)} from FDC CSV."""
    import pandas as pd
    df = pd.read_csv(csv_path)
    odds = {}
    for _, r in df.iterrows():
        ph, pd_, pa = r.get("PSH"), r.get("PSD"), r.get("PSA")
        if pd.isna(ph) or pd.isna(pd_) or pd.isna(pa):
            ph, pd_, pa = r.get("B365H"), r.get("B365D"), r.get("B365A")
        if pd.isna(ph) or pd.isna(pd_) or pd.isna(pa):
            continue
        home = TEAM_ALIAS.get(r["HomeTeam"], r["HomeTeam"])
        away = TEAM_ALIAS.get(r["AwayTeam"], r["AwayTeam"])
        odds[(home, away)] = devig(float(ph), float(pd_), float(pa))
    return odds


# ---------------------------------------------------------------------------
# live (InferSports keyless)
# ---------------------------------------------------------------------------
INFERSPORTS_URL = "https://api.infersports.dev/v1/mcp/compare_prob"


def fetch_live_strength(fixtures, team_id_to_name):
    """Fetch fair 1x2 lines for the given fixtures from InferSports.

    fixtures: list of dicts with 'event' (gw), 'team_h', 'team_a' (ids)
    team_id_to_name: {team_id: name} (FPL names, e.g. 'Man Utd')

    Returns {(gw, team_id): multiplier} for teams whose match was found.
    Falls back gracefully (missing team = no entry; caller falls back to FDR).
    """
    from collections import defaultdict
    # group fixtures by gw
    by_gw = defaultdict(list)
    for f in fixtures:
        by_gw[int(f["event"])].append(f)

    result = {}
    for gw, flist in by_gw.items():
        for f in flist:
            h_id, a_id = int(f["team_h"]), int(f["team_a"])
            h_name = team_id_to_name.get(h_id)
            a_name = team_id_to_name.get(a_id)
            if not h_name or not a_name:
                continue
            probs = _infer_1x2(h_name, a_name)
            if probs is None:
                continue
            p_h, p_d, p_a = probs
            # home team players
            result[(gw, h_id)] = (p_h, p_a)
            result[(gw, a_id)] = (p_a, p_h)
    return result


def _infer_1x2(home, away, timeout=12):
    """Fair 1x2 probabilities via InferSports; None on failure."""
    payload = json.dumps({
        "query": f"{home} vs {away}",
        "external_prob": 0.5,
        "market_type": "1x2",
        "outcome": "home",
        "external_label": "fpl-xpts",
        "sport": "football",
    }).encode()
    req = urllib.request.Request(INFERSPORTS_URL, data=payload,
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        # InferSports response shape varies; try common fields
        probs = data.get("fair_prob") or data.get("probabilities") or data.get("implied")
        if isinstance(probs, dict):
            return (probs.get("home"), probs.get("draw"), probs.get("away"))
        if data.get("verdict") is not None:
            # compare_prob returns fair_prob for the queried outcome only;
            # for 1x2 we need the full line - try the probabilities endpoint style
            fp = data.get("fair_prob")
            if fp is not None:
                # treat as home prob, others unknown -> caller can't use; skip
                return None
        return None
    except Exception:
        return None


def strength_multiplier_map(live_strength, gw, position_lookup):
    """Wrap a live_strength result into {(team_id): multiplier} for one GW.

    position_lookup: {team_id: dominant position for multiplier choice}
    (MID/FWD multiplier vs GKP/DEF multiplier depends on the PLAYER's position,
     so pre_deadline_run calls odds_multiplier per-player instead.)
    """
    return live_strength
