#!/usr/bin/env python3
"""
FPL GW Data Fetcher — pulls picks, points, transfers, chips for every league member.
Run after each GW finishes. Generates structured data for analysis.

Supports multiple leagues. Default: league 58005 (LIGA FPL KK OLD BOYS S5, 737 members)
and league 131997 (OVERALL IFE 26/27, 1816 members).

Usage: python3 fetch_gw_data.py [--gw <num>] [--league 58005 131997] [--max 3000]
"""

import urllib.request
import urllib.error
import json
import time
import os
import sys
import argparse
from collections import defaultdict

BASE_URL = "https://fantasy.premierleague.com/api"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")


def api_get(path, retries=3):
    """Fetch from FPL API with retry.

    HTTP 404 is treated as a definitive "not found" and NOT retried —
    the FPL transfers endpoint returns 404 for any team that made 0
    transfers in a GW. Retrying/backing off on 404 wastes ~3s per hit
    and there are thousands of them.
    """
    url = f"{BASE_URL}/{path.lstrip('/')}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # definitive empty; don't retry
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  ⚠️ Failed {url}: {e}", file=sys.stderr)
                return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  ⚠️ Failed {url}: {e}", file=sys.stderr)
                return None


def load_bootstrap():
    """Load and cache bootstrap data (players, teams, events)."""
    cache_path = os.path.join(DATA_DIR, "bootstrap_cache.json")
    if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < 3600:
        with open(cache_path) as f:
            return json.load(f)

    data = api_get("bootstrap-static/")
    if data:
        with open(cache_path, "w") as f:
            json.dump(data, f)
    return data


def build_player_map(bootstrap):
    """Build player_id -> {name, team, position, cost} mapping."""
    teams = {t['id']: t['name'] for t in bootstrap.get('teams', [])}
    pos_types = {e['id']: e['singular_name_short'] for e in bootstrap.get('element_types', [])}

    players = {}
    for p in bootstrap.get('elements', []):
        players[p['id']] = {
            'name': p['web_name'],
            'full_name': p['first_name'] + ' ' + p['second_name'],
            'team': teams.get(p['team'], 'Unknown'),
            'team_id': p['team'],
            'position': pos_types.get(p['element_type'], '?'),
            'position_id': p['element_type'],
            'cost': p['now_cost'] / 10,
            'form': float(p.get('form', 0) or 0),
            'total_points': p['total_points'],
            'points_per_game': float(p.get('points_per_game', 0) or 0),
            'selected_by': float(p.get('selected_by_percent', 0) or 0),
            'status': p.get('status', 'a'),
            'chance_of_playing': p.get('chance_of_playing_next_round', 100),
            'news': p.get('news', ''),
            # Preserve official underlying fields for the isolated V5 lab.
            'minutes': p.get('minutes', 0), 'starts': p.get('starts', 0),
            'expected_goals': p.get('expected_goals', 0),
            'expected_assists': p.get('expected_assists', 0),
            'expected_goal_involvements': p.get('expected_goal_involvements', 0),
            'expected_goals_conceded': p.get('expected_goals_conceded', 0),
            'expected_goals_per_90': p.get('expected_goals_per_90', 0),
            'expected_assists_per_90': p.get('expected_assists_per_90', 0),
            'expected_goals_conceded_per_90': p.get('expected_goals_conceded_per_90', 0),
            'defensive_contribution': p.get('defensive_contribution', 0),
            'defensive_contribution_per_90': p.get('defensive_contribution_per_90', 0),
            'saves': p.get('saves', 0), 'saves_per_90': p.get('saves_per_90', 0),
            'bonus': p.get('bonus', 0), 'bps': p.get('bps', 0),
            'penalties_order': p.get('penalties_order'),
            'direct_freekicks_order': p.get('direct_freekicks_order'),
            'corners_and_indirect_freekicks_order': p.get('corners_and_indirect_freekicks_order'),
        }
    return players


def get_league_standings(league_id, gw):
    """Get league standings for a given GW. Paginates through all pages.

    NOTE: The FPL standings endpoint must be paginated with the
    ``?page_standings=N&new_entries=0`` parameter form. Using ``?page=N``
    triggers an FPL API quirk where every page returns the FIRST page again
    while ``has_next`` stays ``True`` forever, causing an infinite loop.
    We also cap pages and dedupe by entry_id as a hard safety net.
    """
    data = api_get(f"leagues-classic/{league_id}/standings/")
    if not data:
        # Hard failure on the FIRST standings call — do NOT fall through to a
        # stale/empty substitutes table. FPL returns 503 under heavy load or an
        # outage; retry a few times with backoff before giving up.
        for attempt in range(3):
            print(f"  ⚠️ Standings for league {league_id} empty (retry {attempt + 1}/3)...", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
            data = api_get(f"leagues-classic/{league_id}/standings/")
            if data:
                break
    if not data:
        return []

    results = []
    standings = data.get('standings', {})
    for r in standings.get('results', []):
        results.append({
            'rank': r.get('rank', 0),
            'entry_id': r.get('entry', 0),
            'entry_name': r.get('entry_name', ''),
            'player_name': r.get('player_name', ''),
            'total_points': r.get('total', 0),
            'last_rank': r.get('last_rank', r.get('rank', 0)),
            'rank_sort': r.get('rank_sort', 0),
        })

    # Paginate using page_standings + new_entries form (bounded for safety)
    has_more = standings.get('has_next', False)
    page = 2
    seen_ids = {r['entry_id'] for r in results}
    max_pages = 500
    while has_more and page <= max_pages:
        data = api_get(f"leagues-classic/{league_id}/standings/?page_standings={page}&new_entries=0")
        if not data:
            break
        standings = data.get('standings', {})
        added_new = False
        for r in standings.get('results', []):
            eid = r.get('entry', 0)
            if eid in seen_ids:
                continue  # skip duplicates; loop terminates if page is all dupes
            seen_ids.add(eid)
            added_new = True
            results.append({
                'rank': r.get('rank', 0),
                'entry_id': eid,
                'entry_name': r.get('entry_name', ''),
                'player_name': r.get('player_name', ''),
                'total_points': r.get('total', 0),
                'last_rank': r.get('last_rank', r.get('rank', 0)),
                'rank_sort': r.get('rank_sort', 0),
            })
        has_more = standings.get('has_next', False)
        if not added_new and page > 2:
            # No new entries on this page → standings exhausted (broken endpoint)
            break
        page += 1
        time.sleep(0.3)

    return results


def get_entry_picks(entry_id, gw):
    """Get a team's picks for a specific GW."""
    return api_get(f"entry/{entry_id}/event/{gw}/picks/")


def get_entry_history(entry_id):
    """Get full entry history (all GWs this season)."""
    return api_get(f"entry/{entry_id}/history/")


def get_entry_transfers(entry_id, gw):
    """Get transfers made in a specific GW."""
    return api_get(f"entry/{entry_id}/event/{gw}/transfers/")


def get_entry_live(entry_id):
    """Get live entry data."""
    return api_get(f"entry/{entry_id}/")


def fetch_competitor_data(entry_id, gw, player_map):
    """Fetch all data for a single competitor for a GW."""
    result = {
        'entry_id': entry_id,
        'gw': gw,
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }

    # Entry history
    entry_info = get_entry_history(entry_id)
    if entry_info:
        result['history'] = entry_info
        current = entry_info.get('current', [])
        for gw_entry in current:
            if gw_entry.get('event') == gw:
                result['gw_points'] = gw_entry.get('points', 0)
                result['total_points'] = gw_entry.get('total_points', 0)
                result['rank'] = gw_entry.get('rank', 0)
                result['rank_sort'] = gw_entry.get('rank_sort', 0)
                result['gw_transfers'] = gw_entry.get('event_transfers', 0)
                result['gw_transfers_cost'] = gw_entry.get('event_transfers_cost', 0)
                result['gw_bank'] = gw_entry.get('bank', 0)
                result['gw_value'] = gw_entry.get('value', 0)
                break

    # Picks (squad)
    picks = get_entry_picks(entry_id, gw)
    if picks:
        result['picks'] = picks
        squad = []
        for pick in picks.get('picks', []):
            pid = pick['element']
            player = player_map.get(pid, {})
            squad.append({
                'element': pid,
                'name': player.get('name', f'Player_{pid}'),
                'position': player.get('position', '?'),
                'team': player.get('team', '?'),
                'cost': player.get('cost', 0),
                'is_captain': pick.get('is_captain', False),
                'is_vice_captain': pick.get('is_vice_captain', False),
                'multiplier': pick.get('multiplier', 1),
                'position_order': pick.get('position', 0),
                'selected_by': player.get('selected_by', 0),
                'form': player.get('form', 0),
                'total_points': player.get('total_points', 0),
                'points_per_game': player.get('points_per_game', 0),
                'status': player.get('status', 'a'),
                'chance_of_playing': player.get('chance_of_playing', 100),
                'news': player.get('news', ''),
                'minutes': player.get('minutes', 0), 'starts': player.get('starts', 0),
                'expected_goals': player.get('expected_goals', 0),
                'expected_assists': player.get('expected_assists', 0),
                'expected_goals_per_90': player.get('expected_goals_per_90', 0),
                'expected_assists_per_90': player.get('expected_assists_per_90', 0),
            })
        result['squad'] = squad

        # Captain
        captain = next((s for s in squad if s['is_captain']), None)
        vice = next((s for s in squad if s['is_vice_captain']), None)
        result['captain'] = captain['name'] if captain else 'N/A'
        result['vice_captain'] = vice['name'] if vice else 'N/A'

        # Squad composition — STARTING XI ONLY (multiplier > 0 = on field).
        # Counting all 15 picks yields a meaningless 2-5-5-3 for everyone;
        # the on-field XI is picks with multiplier > 0 (captain = 2, rest = 1,
        # bench = 0). This is the number FPL reports as the formation.
        positions = defaultdict(int)
        starting_xi = defaultdict(int)
        teams_in_squad = defaultdict(int)
        for s in squad:
            positions[s['position']] += 1
            if s['multiplier'] > 0:
                starting_xi[s['position']] += 1
            if s['team'] and s['team'] != '?':
                teams_in_squad[s['team']] += 1
        result['squad_composition'] = dict(positions)
        result['starting_xi_composition'] = dict(starting_xi)
        result['squad_teams'] = dict(teams_in_squad)
        result['squad_cost'] = sum(s['cost'] for s in squad)

        # Active / injured
        active = [s for s in squad if s['status'] == 'a' and (s['chance_of_playing'] is None or s['chance_of_playing'] >= 75)]
        result['active_players_count'] = len(active)
        injured = [s for s in squad if s['status'] != 'a' or (s['chance_of_playing'] is not None and s['chance_of_playing'] < 50)]
        result['injured_players'] = [s['name'] for s in injured]
        result['injured_count'] = len(injured)

    # Transfers
    transfers = get_entry_transfers(entry_id, gw)
    if transfers:
        result['transfers'] = transfers
        transfers_list = transfers.get('transfers', [])
        result['transfers_made'] = len(transfers_list)
        if transfers_list:
            transfer_details = []
            for t in transfers_list:
                out_player = player_map.get(t.get('element_out', 0), {})
                in_player = player_map.get(t.get('element_in', 0), {})
                transfer_details.append({
                    'out': out_player.get('name', f"Player_{t.get('element_out',0)}"),
                    'in': in_player.get('name', f"Player_{t.get('element_in',0)}"),
                    'cost': t.get('element_in_cost', 0) / 10,
                    'sold_for': t.get('element_out_cost', 0) / 10,
                })
            result['transfer_details'] = transfer_details

    # Chips
    if entry_info:
        chips = entry_info.get('chips', [])
        result['chips_used'] = [c for c in chips if c.get('event') == gw]

    return result


def load_entry_ids_from_scout_data(league_ids):
    """Load entry IDs for given leagues from pre-season scout data."""
    path = os.path.join(DATA_DIR, "full_scout_data.json")
    if not os.path.exists(path):
        print(f"⚠️ Scout data not found: {path}", file=sys.stderr)
        return {}

    with open(path) as f:
        data = json.load(f)

    memberships = data.get('memberships', [])
    league_entries = defaultdict(list)

    for m in memberships:
        if not isinstance(m, dict):
            continue
        lid = m.get('league_id')
        if lid and lid in league_ids:
            league_entries[lid].append({
                'entry_id': m.get('entry', 0),
                'entry_name': m.get('entry_name', ''),
                'player_name': m.get('player_name', ''),
            })

    print(f"📋 Loaded entry IDs from scout data:", file=sys.stderr)
    for lid in league_ids:
        print(f"  League {lid}: {len(league_entries[lid])} entries", file=sys.stderr)
    return dict(league_entries)


def main():
    parser = argparse.ArgumentParser(description='FPL GW Data Fetcher')
    parser.add_argument('--gw', type=int, help='Gameweek to fetch (default: auto-detect)')
    parser.add_argument('--league', type=int, nargs='+', default=[58005, 131997],
                        help='League IDs (space-separated, default: 58005 131997)')
    parser.add_argument('--max', type=int, default=3000, help='Max entries to fetch per league')
    parser.add_argument('--output', help='Output file path base')
    parser.add_argument('--force-stale', action='store_true',
                        help='If live standings fetch fails, build the report from stale scout data anyway '
                             '(operator override — normally we abort to avoid emitting misleading data)')
    args = parser.parse_args()

    league_ids = args.league

    # Load bootstrap
    print("📦 Loading bootstrap data...", file=sys.stderr)
    bootstrap = load_bootstrap()
    if not bootstrap:
        print("❌ Failed to load bootstrap static!", file=sys.stderr)
        return 1
    player_map = build_player_map(bootstrap)
    print(f"✅ Loaded {len(player_map)} players, {len(bootstrap.get('teams',[]))} teams", file=sys.stderr)

    # Detect GW
    gw = args.gw
    if not gw:
        now = time.time()
        for e in bootstrap.get('events', []):
            if e.get('finished'):
                gw = e['id']
        if not gw:
            events = [e for e in bootstrap.get('events', []) if e.get('id', 0) > 0]
            finished = [e for e in events if e.get('finished')]
            if finished:
                gw = finished[-1]['id']
            else:
                for e in reversed(events):
                    if e.get('deadline_time_epoch', 0) < now:
                        gw = e['id']
                        break
    if not gw:
        print("❌ Could not determine current GW!", file=sys.stderr)
        return 1
    print(f"🎯 Target: GW {gw}", file=sys.stderr)

    # Collect all unique entry IDs across leagues (deduplicated)
    all_entries_per_league = {}
    all_entry_ids_set = set()

    for lid in league_ids:
        print(f"\n📊 Fetching league {lid} standings...", file=sys.stderr)
        standings = get_league_standings(lid, gw)
        print(f"✅ Found {len(standings)} entries in standings", file=sys.stderr)

        if not standings:
            if args.force_stale:
                # Explicit operator override — only used when you KNOW the live
                # API is down and a stale/zeroed table is acceptable.
                print(f"⚠️ [--force-stale] Standings empty — using scout data for league {lid}", file=sys.stderr)
                scout_entries = load_entry_ids_from_scout_data([lid])
                standings = [{
                    'entry_id': e['entry_id'],
                    'entry_name': e.get('entry_name', ''),
                    'player_name': e.get('player_name', ''),
                    'rank': i + 1,
                    'total_points': 0,
                    'last_rank': 0,
                } for i, e in enumerate(scout_entries.get(lid, []))]
            else:
                # Abort: an empty live standings table during a real (finished)
                # GW means the FPL API is down/returning 503. Producing a report
                # from stale scout data with fabricated ranks and 0 points would
                # be misleading. Fail loudly instead.
                print(f"\n❌ ABORT: live standings for league {lid} returned 0 entries "
                      f"(FPL API unavailable?). Refusing to build a stale report.\n"
                      f"   Fix the outage, then re-run. Use --force-stale ONLY if "
                      f"you explicitly accept a stale-data report.", file=sys.stderr)
                return 1

        # Deduplicate against all_entry_ids_set
        unique_for_league = []
        for s in standings:
            eid = s['entry_id']
            if eid and eid not in all_entry_ids_set:
                all_entry_ids_set.add(eid)
                unique_for_league.append(s)
        all_entries_per_league[lid] = unique_for_league
        print(f"  → {len(unique_for_league)} unique entries after dedup", file=sys.stderr)

    total_unique = len(all_entry_ids_set)
    print(f"\n🎯 Total unique entries: {total_unique}", file=sys.stderr)

    # Fetch data for each unique competitor.
    # Iterate in ascending (best) rank order so that if we hit the --max cap,
    # we drop only the lowest-ranked entries — never elite top teams.
    all_data = {lid: [] for lid in league_ids}
    count = 0
    errors = 0
    entry_id_to_league_entry = {}

    # Build reverse mapping: entry_id -> (league_id, standing_info)
    for lid, entries in all_entries_per_league.items():
        for s in entries:
            eid = s['entry_id']
            if eid not in entry_id_to_league_entry:
                entry_id_to_league_entry[eid] = []
            entry_id_to_league_entry[eid].append({'league_id': lid, 'standing': s})

    # Deterministic, rank-ordered list of entry IDs (best rank first).
    # An entry appearing in multiple leagues uses its best (lowest) rank.
    def _best_rank(infos):
        return min((i['standing'].get('rank', 10 ** 9) for i in infos), default=10 ** 9)

    ordered = sorted(
        entry_id_to_league_entry.keys(),
        key=lambda eid: ( _best_rank(entry_id_to_league_entry[eid]), eid )
    )
    all_entry_ids = ordered
    fetched_so_far = 0
    for i, entry_id in enumerate(all_entry_ids):
        if fetched_so_far >= args.max:
            break
        if i % 50 == 0 and i > 0:
            print(f"  Progress: {i}/{min(len(all_entry_ids), args.max)} (errors: {errors})", file=sys.stderr)

        comp_data = fetch_competitor_data(entry_id, gw, player_map)

        # Assign league info — first league this entry appears in
        league_info = entry_id_to_league_entry.get(entry_id, [])
        if league_info:
            li = league_info[0]
            comp_data['league_id'] = li['league_id']
            comp_data['league_rank'] = li['standing'].get('rank', 0)
            comp_data['entry_name'] = li['standing'].get('entry_name', '')
            comp_data['player_name'] = li['standing'].get('player_name', '')
            comp_data['league_total'] = li['standing'].get('total_points', 0)
            comp_data['league_last_rank'] = li['standing'].get('last_rank', 0)
        else:
            comp_data['league_id'] = 0
            comp_data['league_rank'] = 0

        all_data[comp_data.get('league_id', 0)].append(comp_data)
        count += 1
        fetched_so_far += 1
        time.sleep(0.15)

    # Output — one file per league
    for lid in league_ids:
        output_path = args.output or os.path.join(DATA_DIR, f"gw{gw}_league{lid}_data.json")
        with open(output_path, 'w') as f:
            json.dump({
                'gw': gw,
                'league_id': lid,
                'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'total_entries': len(all_data.get(lid, [])),
                'errors': errors,
                'competitors': all_data.get(lid, []),
            }, f, indent=2)
        print(f"✅ League {lid}: {len(all_data.get(lid, []))} competitors → {output_path}", file=sys.stderr)

        # Compact version
        compact_path = os.path.join(DATA_DIR, f"gw{gw}_league{lid}_compact.json")
        compact = {
            'gw': gw, 'league_id': lid,
            'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'total_entries': len(all_data.get(lid, [])),
            'competitors': [{
                'entry_id': c['entry_id'],
                'entry_name': c.get('entry_name', ''),
                'player_name': c.get('player_name', ''),
                'league_rank': c.get('league_rank', 0),
                'gw_points': c.get('gw_points', 0),
                'total_points': c.get('total_points', 0),
                'rank': c.get('rank', 0),
                'squad_cost': c.get('squad_cost', 0),
                'captain': c.get('captain', 'N/A'),
                'vice_captain': c.get('vice_captain', 'N/A'),
                'transfers_made': c.get('transfers_made', 0),
                'injured_count': c.get('injured_count', 0),
                'active_players_count': c.get('active_players_count', 0),
                'squad_composition': c.get('squad_composition', {}),
                'squad_teams': c.get('squad_teams', {}),
            } for c in all_data.get(lid, [])]
        }
        with open(compact_path, 'w') as f:
            json.dump(compact, f, indent=2)
        print(f"  Compact: {compact_path}", file=sys.stderr)

    print(f"\n✅ Done! {count} total unique competitors across {len(league_ids)} leagues", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
