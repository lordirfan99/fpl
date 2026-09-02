"""
FPL Autopilot - chip wiring.

Maps user-facing chip names to FPL API chip codes and knows which endpoint
each chip type belongs to:

  chip_type "transfer" (wildcard, freehit) -> sent in POST /api/transfers/
  chip_type "team"     (bboost, 3xc)      -> sent in POST /api/my-team/

ALSO: availability windows come from bootstrap-static chips[].start_event /
stop_event. Verified 2026-08-05: wildcard & freehit start GW2 (NOT playable
in GW1); bboost & 3xc start GW1.

P0.7 (7 Aug audit): bootstrap exposes SEPARATE first-half and second-half
allocations for each chip (e.g. wildcard GW2-19 AND GW20-38). fetch_chip_windows
now returns per-allocation window LISTS so a chip played in the first half is
NOT treated as used for its second-half allocation. fetch_used_chips returns
{code: [played_events]} and chip_used_in_window decides availability against
the allocation window that contains the target GW.

Usage:
    from chips import CHIP_API, chip_type, chip_playable_in, chip_endpoint
"""
import json
import urllib.request

# user-facing display name -> FPL API chip code
CHIP_API = {
    "Wildcard": "wildcard",
    "Free Hit": "freehit",
    "Bench Boost": "bboost",
    "Triple Captain": "3xc",
}

# API code -> (chip_type, endpoint suffix)
_CHIP_META = {
    "wildcard": ("transfer", "/api/transfers/"),
    "freehit": ("transfer", "/api/transfers/"),
    "bboost": ("team", "/api/my-team/{entry}/"),
    "3xc": ("team", "/api/my-team/{entry}/"),
}


def chip_api_code(display_name):
    """'Bench Boost' -> 'bboost'. None if unknown."""
    return CHIP_API.get(display_name)


def chip_type(code):
    """'bboost' -> 'team' | 'wildcard' -> 'transfer'."""
    return _CHIP_META.get(code, (None, None))[0]


def chip_endpoint(code, entry=None):
    """Endpoint the chip is sent with. transfer chips -> /api/transfers/,
    team chips -> /api/my-team/{entry}/."""
    t, ep = _CHIP_META.get(code, (None, None))
    if t == "team":
        return ep.format(entry=entry or "{entry}")
    return ep


def _norm_windows(windows):
    """Normalize {code: (s,e)} OR {code: [(s,e), ...]} -> {code: [(s,e), ...]}.

    Backward-compatible with callers/tests that pass single-tuple windows.
    """
    out = {}
    for code, w in (windows or {}).items():
        if isinstance(w, tuple):
            out[code] = [w]
        elif isinstance(w, list) and w and isinstance(w[0], tuple):
            out[code] = list(w)
        elif isinstance(w, list):
            out[code] = [tuple(w)]
        else:
            out[code] = []
    return out


def fetch_chip_windows(timeout=60):
    """{api_code: [(start_event, stop_event), ...]} from bootstrap-static.

    Each chip can have MULTIPLE allocations (first half, second half).
    This is the P0.7 fix: previously the windows were collapsed into one
    union, hiding the valid second-half allocation of a first-half-used chip.
    """
    req = urllib.request.Request("https://fantasy.premierleague.com/api/bootstrap-static/",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    windows = {}
    for c in d.get("chips", []):
        code = c["name"]
        windows.setdefault(code, []).append((c["start_event"], c["stop_event"]))
    return windows


def chip_playable_in(code, gw, windows=None):
    """True if the chip is playable in the given gameweek (any allocation)."""
    if windows is None:
        windows = fetch_chip_windows()
    for start, stop in _norm_windows(windows).get(code, []):
        if start <= gw <= stop:
            return True
    return False


def chip_windows_hint(windows=None):
    """Human-readable availability hint for the bot (per allocation)."""
    if windows is None:
        windows = fetch_chip_windows()
    return {code: " & ".join(f"GW{s}-{e}" for s, e in allocs)
            for code, allocs in _norm_windows(windows).items()}


def fetch_used_chips(entry, timeout=60):
    """{api_code: [played_event, ...]} for chips ALREADY USED this season.

    Reads /api/my-team/{entry}/ -> chips[] (the API marks played chips with
    their event). Values are LISTS so a chip used in its first-half allocation
    can still be available in its second-half allocation (P0.7).
    """
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fpl_client import FPLClient
    team = FPLClient().my_team(entry)
    out = {}
    for c in team.get("chips", []):
        if c.get("status") == "used" or c.get("played_event"):
            out.setdefault(c["name"], []).append(c.get("played_event"))
    return out


def chip_used_in_window(code, gw, used_chips=None, windows=None):
    """True if the chip was ALREADY played in the allocation window containing gw.

    This is the P0.7 availability rule: usage is tracked per bootstrap
    allocation, not per chip name. Playing wildcard in GW10 (first half,
    allocation GW2-19) does NOT suppress the GW24 wildcard (second-half
    allocation GW20-38); the GW24 wildcard is available again.

    used_chips: {code: [played_events]} (accepts {code: event} for back-compat).
    windows:    {code: [(start, stop), ...]} from fetch_chip_windows().
    """
    if not used_chips:
        return False
    played = used_chips.get(code)
    if not played:
        return False
    if isinstance(played, (int, str)):
        played = [played]
    played = [int(x) if x is not None else None for x in played]
    allocs = _norm_windows(windows).get(code, []) if windows else []
    if not allocs:
        # no window info -> any play of this chip blocks it (old behaviour)
        return True
    for start, stop in allocs:
        if start <= gw <= stop:
            # gw falls in this allocation -> used iff played inside it
            return any(pe is not None and start <= pe <= stop for pe in played)
    # gw is outside every known allocation -> can't verify -> fail closed
    return True
