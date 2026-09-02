"""FPL Bonus Points System (BPS) bonus model — v2 (Sol-orchestrated rebuild).

Architecture (per orchestrator directive docs/sol-directive-gw1-edge.md):
  1. PRE-MATCH FEATURE BUILDER  — lagged/EWMA per-90 rates computed strictly
     BEFORE the prediction GW (leakage-safe; current-match realized events are
     NEVER features).
  2. LATENT BPS ESTIMATOR       — per-position regression on actual `bps`
     (the latent score), trained chronologically.
  3. FIXTURE SIMULATION         — sample each player's BPS in a match, rank,
     apply official 3/2/1 with tie rules -> per-player E[bonus], P(any),
     P(1/2/3).
  4. RULE-DELTA LAYER           — versioned 2026/27 BPS coefficient changes
     (config/bps_rules_*.json) applied to simulated BPS as counterfactual.
  5. INFERENCE API              — expected_bonus(row), p_bonus_ge1(row).

Deps: numpy + scipy (inference), pandas (trainer). No sklearn.
Author: DeepSeek worker. Orchestrator: Sol (ChatGPT).
"""
import json
import os

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)
COEFFS_FILE = os.path.join(BASE, "bonus_model_coeffs.json")
RULES_DIR = os.path.join(REPO, "config")

POSITIONS = ("GKP", "DEF", "MID", "FWD")
MEAN_BONUS_GIVEN_POS = 2.0

# Feature order (pre-match only). All rates are per-90, lagged before cutoff.
FEATURES = [
    "minutes_share",        # prior-GW minutes / (gw_so_far*90)
    "bps90_ewma",           # lagged EWMA per-90 BPS
    "goals90_ewma",
    "assists90_ewma",
    "xg90_ewma",
    "xa90_ewma",
    "saves90_ewma",
    "recoveries90_ewma",
    "tackles90_ewma",
    "cbi90_ewma",
    "opp_strength",
    "was_home",
]


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# Rules (versioned, official-source only)
# ---------------------------------------------------------------------------

DEFAULT_RULES_2025 = {
    # Official 2025/26 BPS: CBI = 1 BPS per 2 actions
    "cbi_per_bps": 2.0,
    "tackle_penalty_per_loss": 0.0,   # no official quantifiable value -> 0 delta
    "gk_save_rework": 0.0,
    "meta": {"source": "official 2025/26 FPL rules", "retrieved": "2026-08-12"},
}

DEFAULT_RULES_2026 = {
    # Official 2026/27 BPS change (Onside impact page, snapshot 2026-07-27):
    # clearances/blocks/interceptions = 1 BPS per 3 actions (was 1 per 2).
    # Tackle-penalty removal and GK save rework are NOT officially quantified
    # (FPL does not publish times-dribbled-past / shot classification), so per
    # orchestrator constraint they stay 0-delta instead of invented weights.
    "cbi_per_bps": 3.0,
    "tackle_penalty_per_loss": 0.0,
    "gk_save_rework": 0.0,
    "meta": {"source": "onsidearena.com/bps-impact + official 2026/27 rules", "retrieved": "2026-08-12"},
}


def load_rules(rules_version="2026-27"):
    path = os.path.join(RULES_DIR, f"bps_rules_{rules_version.replace('-', '_')}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return dict(DEFAULT_RULES_2026 if rules_version == "2026-27" else DEFAULT_RULES_2025)


def save_rules(rules, rules_version="2026-27"):
    path = os.path.join(RULES_DIR, f"bps_rules_{rules_version.replace('-', '_')}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=1)
    return path


# ---------------------------------------------------------------------------
# Inference API
# ---------------------------------------------------------------------------

def load_coeffs():
    with open(COEFFS_FILE, encoding="utf-8") as f:
        return json.load(f)


def p_bonus_ge1(row, coeffs=None):
    """P(any bonus) from a pre-match feature dict (row keys = FEATURES + position)."""
    coeffs = coeffs or load_coeffs()
    pos = row.get("position", "MID")
    if pos not in coeffs:
        pos = "MID"
    w = np.array(coeffs[pos]["weights"], dtype=float)
    b = float(coeffs[pos]["intercept"])
    x = np.array([float(row.get(f, 0.0) or 0.0) for f in FEATURES], dtype=float)
    return float(_sigmoid(float(np.dot(w, x)) + b))


def expected_bonus_unconditional(row, coeffs=None, rules_version="2026-27"):
    """Unconditional E[bonus] in [0,3]. Minutes uncertainty already inside the
    feature minutes_share, so callers must NOT re-multiply by play_prob."""
    p = p_bonus_ge1(row, coeffs)
    return _clamp(p * MEAN_BONUS_GIVEN_POS, 0.0, 3.0)


# ---------------------------------------------------------------------------
# Pre-match feature builder (leakage-safe)
# ---------------------------------------------------------------------------

_EWMA_ALPHA = 0.4


def _ewma(values, alpha=_EWMA_ALPHA):
    """EWMA over a list (oldest->newest). Returns None if empty."""
    if not values:
        return None
    acc = None
    for v in values:
        acc = v if acc is None else alpha * v + (1 - alpha) * acc
    return acc


def build_prematch_features(player_gws, team_strength, gw_so_far):
    """For each player-GW row compute a feature dict using ONLY prior GWs.

    player_gws: list of dicts sorted by round, each with the vaastav per-match
    fields (minutes, goals_scored, ... bonus). gw_so_far: prediction horizon GW
    index minus 1 (rows with round <= gw_so_far are history; round == gw_so_far+1
    is the target row and is EXCLUDED from features).
    """
    rows = []
    for r in player_gws:
        rnd = int(r["round"])
        if rnd != gw_so_far + 1:
            continue  # only produce features for the target GW
        hist = [h for h in player_gws if int(h["round"]) <= gw_so_far]
        # per-90 lagged EWMA rates from history only
        def rate(key, min_minutes=1):
            vals = []
            for h in hist:
                if (h.get("minutes") or 0) >= min_minutes:
                    vals.append((h.get(key, 0.0) or 0.0) * 90.0 / max(h["minutes"], 1))
            return _ewma(vals) or 0.0

        played_min = sum((h.get("minutes") or 0) for h in hist)
        min_share = _clamp(played_min / max(gw_so_far * 90.0, 1.0), 0.0, 1.0)
        try:
            opp_s = team_strength.get(int(r.get("opponent_team")), 3.0)
        except (TypeError, ValueError):
            opp_s = 3.0
        rows.append({
            "position": r.get("position"),
            "minutes_share": min_share,
            "bps90_ewma": rate("bps"),
            "goals90_ewma": rate("goals_scored"),
            "assists90_ewma": rate("assists"),
            "xg90_ewma": rate("expected_goals"),
            "xa90_ewma": rate("expected_assists"),
            "saves90_ewma": rate("saves"),
            "recoveries90_ewma": rate("recoveries"),
            "tackles90_ewma": rate("tackles"),
            "cbi90_ewma": rate("clearances_blocks_interceptions"),
            "opp_strength": opp_s,
            "was_home": int(r.get("was_home", 0) or 0),
            "bonus": float(r.get("bonus", 0) or 0),
            "bps_actual": float(r.get("bps", 0) or 0),
            "cbi_actual": float(r.get("clearances_blocks_interceptions", 0) or 0),
            "fixture": r.get("fixture"),
        })
    return rows


# ---------------------------------------------------------------------------
# Trainer: per-position ridge-logistic on P(bonus>=1); BPS estimator unused
# as the bonus target directly (fixture simulation uses BPS latent).
# ---------------------------------------------------------------------------

def train(rows, l2=1.0):
    try:
        from scipy.optimize import minimize
        _HAVE_SCIPY = True
    except Exception:
        _HAVE_SCIPY = False

    def fit(X, y):
        mu = X.mean(axis=0)
        sd = X.std(axis=0)
        sd[sd < 1e-9] = 1.0
        Xs = (X - mu) / sd

        def nll(theta):
            w, b = theta[:-1], theta[-1]
            p = _sigmoid(Xs @ w + b)
            eps = 1e-9
            return -float(np.sum(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))) + 0.5 * l2 * float(w @ w)

        def grad(theta):
            w, b = theta[:-1], theta[-1]
            p = _sigmoid(Xs @ w + b)
            err = p - y
            return np.concatenate([Xs.T @ err + l2 * w, [float(np.sum(err))]])

        theta0 = np.zeros(len(FEATURES) + 1)
        if _HAVE_SCIPY:
            res = minimize(nll, theta0, jac=grad, method="L-BFGS-B", options={"maxiter": 300})
            theta = res.x
        else:
            theta = theta0.copy()
            for _ in range(300):
                theta -= 0.1 * grad(theta)
        return {
            "weights": (theta[:-1] / sd).tolist(),
            "intercept": float(theta[-1] - float(((mu / sd) @ theta[:-1]))),
            "scale_mean": mu.tolist(),
            "scale_sd": sd.tolist(),
        }

    coeffs = {}
    X_all, y_all = [], []
    for pos in POSITIONS:
        X, y = [], []
        for r in rows:
            if r.get("position") != pos:
                continue
            X.append([float(r.get(f, 0.0) or 0.0) for f in FEATURES])
            y.append(1.0 if float(r.get("bonus", 0.0) or 0.0) >= 1 else 0.0)
        if len(X) >= 50 and sum(y) >= 10:
            coeffs[pos] = fit(np.array(X), np.array(y))
            coeffs[pos]["n"] = len(X)
            coeffs[pos]["n_pos"] = int(sum(y))
        X_all.extend(X)
        y_all.extend(y)
    X_all = np.array(X_all)
    y_all = np.array(y_all)
    coeffs["pooled"] = fit(X_all, y_all)
    coeffs["pooled"]["n"] = len(X_all)
    coeffs["pooled"]["n_pos"] = int(sum(y_all))
    for pos in POSITIONS:
        if pos not in coeffs:
            coeffs[pos] = dict(coeffs["pooled"])
    return coeffs


def save_coeffs(coeffs, path=COEFFS_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(coeffs, f, indent=1)
    return path


# ---------------------------------------------------------------------------
# Latent BPS estimator (ridge regression on actual bps, pre-match features)
# ---------------------------------------------------------------------------

def train_bps_regressor(rows, l2=1.0):
    """Per-position ridge regression predicting latent BPS (the actual score).

    rows: feature rows with bps_actual. Returns coeffs dict compatible with
    the logistic schema but under key 'bps_reg' per position.
    """
    try:
        _HAVE_SCIPY = True
    except Exception:
        _HAVE_SCIPY = False

    def fit(X, y):
        mu = X.mean(axis=0)
        sd = X.std(axis=0)
        sd[sd < 1e-9] = 1.0
        Xs = (X - mu) / sd
        # ridge closed-form: (X'X + l2 I)^-1 X'y
        A = Xs.T @ Xs + l2 * np.eye(Xs.shape[1])
        b = np.linalg.solve(A, Xs.T @ y)
        resid = y - Xs @ b
        return {
            "weights": (b / sd).tolist(),
            "intercept": float(-float(((mu / sd) @ b))),
            "scale_mean": mu.tolist(),
            "scale_sd": sd.tolist(),
            "resid_std": float(np.std(resid)),
            "n": int(len(X)),
        }

    coeffs = {}
    for pos in POSITIONS:
        X, y = [], []
        for r in rows:
            if r.get("position") != pos:
                continue
            X.append([float(r.get(f, 0.0) or 0.0) for f in FEATURES])
            y.append(float(r.get("bps_actual", 0.0) or 0.0))
        if len(X) >= 50:
            coeffs[pos] = fit(np.array(X), np.array(y))
    return coeffs


def predict_bps_mean(row, coeffs):
    """Predicted latent BPS for a pre-match feature row."""
    pos = row.get("position", "MID")
    if pos not in coeffs:
        pos = "MID" if "MID" in coeffs else next(iter(coeffs))
    c = coeffs[pos]
    w = np.array(c["weights"], dtype=float)
    b = float(c["intercept"])
    x = np.array([float(row.get(f, 0.0) or 0.0) for f in FEATURES], dtype=float)
    return float(np.dot(w, x)) + b


def bps_resid_std(coeffs, pos="MID"):
    c = coeffs.get(pos) or next(iter(coeffs.values()))
    return float(c.get("resid_std", 8.0))


# ---------------------------------------------------------------------------
# Fixture simulation (official 3/2/1 + tie rules)
# ---------------------------------------------------------------------------

def allocate_bonus(bps_scores):
    """Allocate 3/2/1 bonus to the top-3 BPS in a match, with official ties.

    bps_scores: list of (player_key, bps_float). Returns dict key -> bonus pts.
    Tie rule (official FPL): a group of k players tied on the same BPS share
    the CUMULATIVE bonus pool for the band positions they occupy. Two tied for
    1st share 3+2=5 (2.5 each); three tied for 1st share 3+2+1=6 (2 each);
    two tied for 2nd share 2+1=3 (1.5 each). Players beyond the 3rd position
    in a tie receive nothing.
    """
    if not bps_scores:
        return {}
    ranked = sorted(bps_scores, key=lambda kv: -kv[1])
    bands = [3.0, 2.0, 1.0]
    result = {}
    i = 0
    n = len(ranked)
    while i < n and i < 3:
        # find the tied group starting at position i
        j = i
        while j < n and abs(ranked[j][1] - ranked[i][1]) < 1e-9:
            j += 1
        k = j - i
        pool = sum(bands[i:min(i + k, 3)])
        share = pool / k
        for key, _ in ranked[i:j]:
            result[key] = result.get(key, 0.0) + share
        i = j
    return result


def simulate_fixture(players, n_sims=2000, seed=42, rules=None, coeffs=None,
                     bps_sigma=8.0, cbi_expected=None):
    """players: list of dicts with keys id, position, bps_mean (predicted latent
    BPS) plus pre-match features. Returns {id: {'e_bonus','p_any','p1','p2','p3'}}.

    Rule delta: 2026/27 CBI 1-per-3 vs 2025/26 1-per-2. Predicted BPS (trained on
    the 2025/26 scale) is corrected by subtracting the CBI contribution delta:
    expected_cbi * (1/2 - 1/3) is added to the OLD-scale BPS -> effectively
    re-scaled. Simpler: bps_adj = bps_mean - expected_cbi * (1/cbi_old - 1/cbi_new).
    """
    rng = np.random.default_rng(seed)
    rules = rules or load_rules("2026-27")
    cbi_old = float(rules.get("cbi_per_bps_2025", 2.0)) if "cbi_per_bps_2025" in rules else 2.0
    cbi_new = float(rules.get("cbi_per_bps", 3.0))
    ids = [p["id"] for p in players]
    n = len(players)
    if n == 0:
        return {}
    mu = np.array([float(p.get("bps_mean", 0.0) or 0.0) for p in players])
    # CBI adjustment per player (expected CBI from pre-match cbi90_ewma)
    cbi_adj = np.array([
        (float(p.get("cbi90_ewma", 0.0) or 0.0)) * (1.0 / cbi_old - 1.0 / cbi_new)
        for p in players
    ])
    mu_adj = mu - cbi_adj  # lower CBI-heavy defenders' latent BPS under 26/27 rules
    out = {i: {"e_bonus": 0.0, "p_any": 0.0, "p1": 0.0, "p2": 0.0, "p3": 0.0} for i in ids}
    sims = rng.normal(0, bps_sigma, size=(n_sims, n)) + mu_adj[None, :]
    for s in sims:
        alloc = allocate_bonus(list(zip(ids, s.tolist())))
        for i in ids:
            b = alloc.get(i, 0.0)
            out[i]["e_bonus"] += b / n_sims
            if b >= 1: out[i]["p_any"] += 1.0 / n_sims
            if b >= 1.49: out[i]["p1"] += 1.0 / n_sims
            if b >= 2.49: out[i]["p2"] += 1.0 / n_sims
            if b >= 2.99: out[i]["p3"] += 1.0 / n_sims
    return out


if __name__ == "__main__":
    print("bonus_model v2 module loaded")
