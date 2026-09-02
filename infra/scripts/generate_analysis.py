#!/usr/bin/env python3
"""
FPL GW Analysis Report — generates comprehensive analysis of league competitors.
Reads from gw{gw}_data.json and produces reports.

Usage: python3 generate_analysis.py --gw <num> [--league 58005] [--data-dir <path>] [--output-dir <path>]
"""

import json
import os
import sys
import argparse
from collections import defaultdict, Counter
from datetime import datetime

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

POSITION_ORDER = {'GKP': 0, 'DEF': 1, 'MID': 2, 'FWD': 3}

def load_data(data_path):
    with open(data_path) as f:
        return json.load(f)

def load_compact_data(gw, league_id, data_dir=DATA_DIR):
    path = os.path.join(data_dir, f"gw{gw}_league{league_id}_compact.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None

def load_full_data(gw, league_id, data_dir=DATA_DIR):
    path = os.path.join(data_dir, f"gw{gw}_league{league_id}_data.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None

def load_previous_scout_data(data_dir=DATA_DIR):
    """Load pre-season scout data for tier info from scout_report.csv.

    CSV columns include: entry_id, team_name, manager_name, seasons_played,
    best_rank, scout_score, threat_tier (S/A/B/C/D), archetype.
    Returns {entry_id: {tier, overall_score, best_rank, seasons, ...}} for ELITE WATCH.
    """
    import csv
    path = os.path.join(data_dir, "scout_report.csv")
    TIER_MAP = {'S': 'ELITE', 'A': 'SHARP', 'B': 'SOLID', 'C': 'CASUAL', 'D': 'ROOKIE'}
    result = {}
    if not os.path.exists(path):
        print(f"⚠️ scout_report.csv not found: {path}", file=sys.stderr)
        return result
    with open(path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            try:
                eid = int(row['entry_id'])
            except (ValueError, KeyError):
                continue
            result[eid] = {
                'entry_id': eid,
                'team_name': row.get('team_name', ''),
                'manager_name': row.get('manager_name', ''),
                'tier': TIER_MAP.get(row.get('threat_tier', ''), row.get('threat_tier', '')),
                'overall_score': row.get('scout_score', '?'),
                'best_rank': row.get('best_rank', '?'),
                'seasons': row.get('seasons_played', '?'),
                'archetype': row.get('archetype', ''),
                'recent_rank': row.get('recent_rank', '?'),
                'weighted_percentile': row.get('weighted_percentile', '?'),
            }
    print(f"✅ Loaded {len(result)} scout records from CSV", file=sys.stderr)
    return result

def analyze_squad_ownership(competitors, player_map=None):
    """Analyze which players are most owned across the league."""
    all_players = defaultdict(int)  # player_name -> count
    captain_choices = defaultdict(int)
    formation_counts = defaultdict(int)
    template_players = defaultdict(int)  # players owned by >50% of teams
    
    # Budget tracking
    budgets = []
    total_points_list = []
    gw_points_list = []
    
    for comp in competitors:
        squad = comp.get('squad', [])
        if not squad:
            continue
        
        # Track budgets
        budgets.append(comp.get('squad_cost', 0))
        total_points_list.append(comp.get('total_points', 0) or 0)
        gw_points_list.append(comp.get('gw_points', 0) or 0)
        
        # Count players
        for s in squad:
            all_players[s['name']] += 1
        
        # Captain choices
        cap = comp.get('captain', '')
        if cap:
            captain_choices[cap] += 1
        
        # Formation — use the STARTING XI composition (fetch now records it
        # separately from squad_composition, which counts all 15 picks).
        # Fall back to squad_composition for older/fetched-without-field data,
        # but flag it since it's the 15-pick spread, not the on-field shape.
        comp_data = comp.get('starting_xi_composition') or comp.get('squad_composition', {})
        gkp = comp_data.get('GKP', 0)
        deff = comp_data.get('DEF', 0)
        mid = comp_data.get('MID', 0)
        fwd = comp_data.get('FWD', 0)
        formation = f"{gkp}-{deff}-{mid}-{fwd}"
        formation_counts[formation] += 1
    
    total = len(competitors)
    
    # Ownership percentages
    ownership = {}
    template_players_list = []
    for name, count in sorted(all_players.items(), key=lambda x: -x[1]):
        pct = round(count / total * 100, 1)
        ownership[name] = {'count': count, 'percentage': pct}
        if pct >= 50:
            template_players_list.append(name)
    
    # Sort captains
    capped = sorted(captain_choices.items(), key=lambda x: -x[1])
    
    # Stats
    avg_budget = round(sum(budgets) / len(budgets), 2) if budgets else 0
    avg_total = round(sum(total_points_list) / len(total_points_list), 1) if total_points_list else 0
    avg_gw = round(sum(gw_points_list) / len(gw_points_list), 1) if gw_points_list else 0
    
    return {
        'total_teams': total,
        'ownership': ownership,
        'captain_choices': [{'name': n, 'count': c, 'percentage': round(c/total*100, 1)} for n, c in capped[:20]],
        'formations': sorted(formation_counts.items(), key=lambda x: -x[1]),
        'template_players': template_players_list,
        'avg_budget': avg_budget,
        'avg_total_points': avg_total,
        'avg_gw_points': avg_gw,
        'max_gw_points': max(gw_points_list) if gw_points_list else 0,
        'min_gw_points': min(gw_points_list) if gw_points_list else 0,
    }

def analyze_differentials(competitors, ownership, threshold=10):
    """Identify differentials — players owned by < threshold% of the league."""
    differentials = []
    for name, data in ownership.items():
        if data['percentage'] < threshold and data['percentage'] > 0:
            # Find which teams own this player
            owners = []
            for comp in competitors:
                squad = comp.get('squad', [])
                for s in squad:
                    if s['name'] == name:
                        owners.append(comp.get('entry_name', f"ID:{comp['entry_id']}"))
                        break
            differentials.append({
                'name': name,
                'ownership': data['percentage'],
                'owners': owners[:5],  # Only show first 5
                'total_owners': len(owners),
            })
    return sorted(differentials, key=lambda x: x['ownership'])

def analyze_captain_failures(competitors, ownership):
    """Analyze captain choices — who captained vs who scored well."""
    # For each popular captain, check how many teams captained them
    return []

def analyze_top_performers(competitors):
    """Analyze the top 20 performers of the GW."""
    sorted_comps = sorted(competitors, key=lambda c: (c.get('gw_points', 0) or 0), reverse=True)
    return sorted_comps[:20]

def analyze_bottom_performers(competitors):
    """Analyze bottom 20 performers."""
    sorted_comps = sorted(competitors, key=lambda c: (c.get('gw_points', 0) or 0))
    return sorted_comps[:20]

def analyze_transfers(competitors):
    """Analyze transfer patterns across the league."""
    total_transfers = 0
    teams_with_transfers = 0
    most_transferred_out = defaultdict(int)
    most_transferred_in = defaultdict(int)
    hit_takers = 0
    
    for comp in competitors:
        # PRIMARY count: the FPL detail endpoint (/event/{gw}/transfers/) returns
        # HTTP 404 even for teams that DID transfer, so it can't be trusted alone.
        # The authoritative count is gw_transfers (= history event_transfers),
        # which is populated for every GW regardless. Prefer it, then top up with
        # raw transfer-detail length from the (unreliable) endpoint when present.
        detail_list = comp.get('transfers', {}).get('transfers', [])
        gw_tx = comp.get('gw_transfers', 0) or 0
        n_transfers = max(gw_tx, len(detail_list))
        if n_transfers > 0:
            teams_with_transfers += 1
            total_transfers += n_transfers

            # Check for hits
            cost = comp.get('gw_transfers_cost', 0)
            if cost > 0:
                hit_takers += 1

            for t in detail_list:
                td = comp.get('transfer_details', [])
                for detail in td:
                    most_transferred_out[detail['out']] += 1
                    most_transferred_in[detail['in']] += 1
    
    return {
        'total_transfers': total_transfers,
        'teams_with_transfers': teams_with_transfers,
        'pct_transferred': round(teams_with_transfers / len(competitors) * 100, 1) if competitors else 0,
        'hit_takers': hit_takers,
        'most_transferred_out': sorted(most_transferred_out.items(), key=lambda x: -x[1])[:10],
        'most_transferred_in': sorted(most_transferred_in.items(), key=lambda x: -x[1])[:10],
        'avg_transfers_per_team': round(total_transfers / max(teams_with_transfers, 1), 1),
    }

def analyze_injuries(competitors):
    """Analyze injury situations across the league."""
    all_injured = defaultdict(int)
    total_injured = 0
    
    for comp in competitors:
        injured = comp.get('injured_players', [])
        for p in injured:
            all_injured[p] += 1
            total_injured += 1
    
    return {
        'total_injured_instances': total_injured,
        'avg_injured_per_team': round(total_injured / len(competitors), 1) if competitors else 0,
        'most_common_injured': sorted(all_injured.items(), key=lambda x: -x[1])[:10],
    }

def analyze_team_clusters(competitors):
    """Analyze which clubs are most represented in squads."""
    all_teams = defaultdict(int)
    
    for comp in competitors:
        teams = comp.get('squad_teams', {})
        for team, count in teams.items():
            all_teams[team] += count
    
    return sorted(all_teams.items(), key=lambda x: -x[1])

def analyze_formation_distribution(competitors):
    """Analyze formation patterns."""
    formations = defaultdict(int)
    for comp in competitors:
        comp_data = comp.get('squad_composition', {})
        gkp = comp_data.get('GKP', 0)
        deff = comp_data.get('DEF', 0)
        mid = comp_data.get('MID', 0)
        fwd = comp_data.get('FWD', 0)
        formation = f"{deff}-{mid}-{fwd}"
        formations[formation] += 1
    return sorted(formations.items(), key=lambda x: -x[1])

def generate_markdown_report(gw, analysis, competitors_full, output_dir, league_id=58005, league_name=None):
    """Generate a comprehensive markdown report."""
    lines = []
    lines.append(f"# FPL League {league_id} — Gameweek {gw} Analysis\n")
    lines.append(f"> **Report generated:** {datetime.utcnow().strftime('%d %B %Y %H:%M UTC')}")
    lines.append(f"> **League:** {league_name or 'League ' + str(league_id)} — [standings](https://fantasy.premierleague.com/leagues/{league_id}/standings/c)")
    lines.append(f"> **Total competitors analysed:** {analysis['squad_ownership']['total_teams']}\n")
    
    # --- GW Summary ---
    lines.append("---\n")
    lines.append("## 📊 GW Summary\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|:-------|:------|")
    lines.append(f"| Average GW Points | {analysis['squad_ownership']['avg_gw_points']} |")
    lines.append(f"| Highest GW Points | {analysis['squad_ownership']['max_gw_points']} |")
    lines.append(f"| Lowest GW Points | {analysis['squad_ownership']['min_gw_points']} |")
    lines.append(f"| Average Total Points | {analysis['squad_ownership']['avg_total_points']} |")
    lines.append(f"| Average Squad Cost | £{analysis['squad_ownership']['avg_budget']}m |")
    lines.append(f"| Teams Making Transfers | {analysis['transfers']['teams_with_transfers']} ({analysis['transfers']['pct_transferred']}%) |")
    lines.append(f"| -4pt Hits Taken | {analysis['transfers']['hit_takers']} teams |")
    lines.append(f"| Total Transfers Made | {analysis['transfers']['total_transfers']} |")
    lines.append(f"| Chips Used (GW{gw}) | {dict(analysis.get('chips', {}))} |\n")
    
    # --- Top 20 ---
    lines.append("---\n")
    lines.append("## 🏆 Top 20 Performers — GW{}\n".format(gw))
    lines.append("| Rank | Team | Manager | GW Pts | Total | Captain | Transfers |")
    lines.append("|:----:|:-----|:--------|:------:|:-----:|:--------|:---------:|")
    for i, comp in enumerate(analysis['top_performers'], 1):
        cap = comp.get('captain', 'N/A')
        transfers = comp.get('transfers_made', 0)
        lines.append(f"| {i} | {comp.get('entry_name','?')} | {comp.get('player_name','?')} | {comp.get('gw_points',0)} | {comp.get('total_points',0)} | {cap} | {transfers} |")
    
    # --- Bottom 20 ---
    lines.append("\n---\n")
    lines.append("## ⚠️ Bottom 20 Performers — GW{}\n".format(gw))
    lines.append("| Rank | Team | Manager | GW Pts | Total | Captain | Transfers |")
    lines.append("|:----:|:-----|:--------|:------:|:-----:|:--------|:---------:|")
    for i, comp in enumerate(analysis['bottom_performers'], 1):
        cap = comp.get('captain', 'N/A')
        transfers = comp.get('transfers_made', 0)
        lines.append(f"| {i} | {comp.get('entry_name','?')} | {comp.get('player_name','?')} | {comp.get('gw_points',0)} | {comp.get('total_points',0)} | {cap} | {transfers} |")
    
    # --- Ownership ---
    lines.append("\n---\n")
    lines.append("## 🧩 Most Owned Players (League Template)\n")
    ownership = analysis['squad_ownership']['ownership']
    top_owned = sorted(ownership.items(), key=lambda x: -x[1]['percentage'])[:20]
    lines.append("| Player | Ownership | Teams |")
    lines.append("|:-------|:---------:|:-----:|")
    for name, data in top_owned:
        lines.append(f"| {name} | {data['percentage']}% | {data['count']} |")
    
    if analysis['squad_ownership']['template_players']:
        lines.append(f"\n**Template Players (≥50%):** {', '.join(analysis['squad_ownership']['template_players'])}\n")
    
    # --- Captain Choices ---
    lines.append("\n---\n")
    lines.append("## 🎯 Captain Choices\n")
    lines.append("| Player | Captained By | % of League |")
    lines.append("|:-------|:-----------:|:---------:|")
    for cap in analysis['squad_ownership']['captain_choices'][:10]:
        lines.append(f"| {cap['name']} | {cap['count']} teams | {cap['percentage']}% |")
    
    # --- Differentials ---
    lines.append("\n---\n")
    lines.append("## 🎲 Differentials (Owned < 10%)\n")
    for diff in analysis['differentials'][:20]:
        lines.append(f"- **{diff['name']}** — {diff['ownership']}% owned ({diff['total_owners']} teams)")
        if diff['owners']:
            lines.append(f"  - Owners: {', '.join(diff['owners'][:3])}")
    
    # --- Transfers ---
    lines.append("\n---\n")
    lines.append("## 🔄 Transfer Market\n")
    lines.append("### Most Transferred IN\n")
    if analysis['transfers']['most_transferred_in']:
        lines.append("| Player | Teams |")
        lines.append("|:-------|:-----:|")
        for name, count in analysis['transfers']['most_transferred_in']:
            lines.append(f"| {name} | {count} |")
    
    lines.append("\n### Most Transferred OUT\n")
    if analysis['transfers']['most_transferred_out']:
        lines.append("| Player | Teams |")
        lines.append("|:-------|:-----:|")
        for name, count in analysis['transfers']['most_transferred_out']:
            lines.append(f"| {name} | {count} |")
    
    # --- Formations ---
    lines.append("\n---\n")
    lines.append("## 🏗️ Formation Distribution\n")
    lines.append("| Formation | Teams | % |")
    lines.append("|:---------:|:-----:|:-:|")
    total_teams = analysis['squad_ownership']['total_teams']
    for formation, count in analysis['formations']:
        pct = round(count / total_teams * 100, 1)
        lines.append(f"| {formation} | {count} | {pct}% |")
    
    # --- Injuries ---
    if analysis['injuries']['total_injured_instances'] > 0:
        lines.append("\n---\n")
        lines.append("## 🏥 Injury Watch\n")
        lines.append(f"- **Total injured players across league:** {analysis['injuries']['total_injured_instances']}")
        lines.append(f"- **Average per team:** {analysis['injuries']['avg_injured_per_team']}")
        if analysis['injuries']['most_common_injured']:
            lines.append("\n**Most Common Injuries:**")
            for name, count in analysis['injuries']['most_common_injured']:
                lines.append(f"- {name}: {count} teams")
    
    # --- Team Clusters ---
    lines.append("\n---\n")
    lines.append("## 🏟️ Club Representation\n")
    lines.append("| Club | Players in Squads |")
    lines.append("|:-----|:-----------------:|")
    for team, count in analysis['team_clusters']:
        lines.append(f"| {team} | {count} |")
    
    # --- Footer ---
    lines.append("\n---\n")
    lines.append("*Generated by FPL Scout*")
    
    return "\n".join(lines)

def generate_elite_watch_report(gw, competitors, scout_data, output_dir):
    """Generate detailed analysis of known ELITE/SHARP managers."""
    elite_ids = {eid for eid, data in scout_data.items() if data.get('tier') in ('ELITE',)}
    sharp_ids = {eid for eid, data in scout_data.items() if data.get('tier') in ('SHARP',)}
    
    # Find competitors in these tiers
    elite_comps = [c for c in competitors if c['entry_id'] in elite_ids]
    sharp_comps = [c for c in competitors if c['entry_id'] in sharp_ids]
    top100 = sorted(competitors, key=lambda c: (c.get('gw_points', 0) or 0), reverse=True)[:100]
    
    lines = []
    lines.append(f"# Elite & Sharp Manager Watch — GW{gw}\n")
    lines.append(f"> **Focus report:** tracking known high-threat managers from pre-season scout\n")
    lines.append(f"> **ELITE managers tracked:** {len(elite_comps)} | **SHARP managers tracked:** {len(sharp_comps)}\n")
    
    # ELITE managers
    lines.append("---\n")
    lines.append(f"## 🔴 ELITE Managers ({len(elite_comps)})\n")
    if elite_comps:
        lines.append("| # | Team | Manager | Tier | GW Pts | Total | Captain | Squad Cost |")
        lines.append("|:-:|:-----|:--------|:----:|:------:|:-----:|:--------|:----------:|")
        for i, comp in enumerate(sorted(elite_comps, key=lambda c: (c.get('gw_points',0) or 0), reverse=True), 1):
            sd = scout_data.get(comp['entry_id'], {})
            cap = comp.get('captain', 'N/A')
            lines.append(f"| {i} | {comp.get('entry_name','?')} | {comp.get('player_name','?')} | {sd.get('overall_score','?')} | {comp.get('gw_points',0)} | {comp.get('total_points',0)} | {cap} | £{comp.get('squad_cost',0)}m |")
        
        # Detailed squad analysis for each ELITE
        lines.append("\n### Squad Details\n")
        for comp in sorted(elite_comps, key=lambda c: (c.get('gw_points',0) or 0), reverse=True):
            sd = scout_data.get(comp['entry_id'], {})
            lines.append(f"\n#### {comp.get('entry_name','?')} ({comp.get('player_name','?')}) — GW: {comp.get('gw_points',0)}pts")
            lines.append(f"- **Pre-season score:** {sd.get('overall_score','?')}/100 | **Best rank:** {sd.get('best_rank','?')} | **Seasons:** {sd.get('seasons','?')}")
            lines.append(f"- **Captain:** {comp.get('captain','N/A')} | **VC:** {comp.get('vice_captain','N/A')}")
            lines.append(f"- **Squad cost:** £{comp.get('squad_cost',0)}m | **Formation:** {comp.get('squad_composition',{})}")
            lines.append(f"- **Transfers:** {comp.get('transfers_made',0)} | **Injured:** {comp.get('injured_count',0)}")
            if comp.get('transfer_details'):
                lines.append(f"- **Transfer details:**")
                for td in comp['transfer_details']:
                    lines.append(f"  - {td['out']} → {td['in']} (£{td['sold_for']}m → £{td['cost']}m)")
            # Squad list
            if comp.get('squad'):
                lines.append(f"\n  | Pos | Player | Cost | Team | C/VC |")
                lines.append(f"  |:---:|:-------|:----:|:----:|:----:|")
                for s in sorted(comp['squad'], key=lambda x: (POSITION_ORDER.get(x['position'], 99), x.get('position_order', 0))):
                    cap_mark = "🅲" if s['is_captain'] else ("🆅" if s['is_vice_captain'] else "")
                    status_mark = "⚠️" if s.get('status', 'a') != 'a' else ""
                    lines.append(f"  | {s['position']} | {s['name']} {status_mark} | £{s['cost']}m | {s['team']} | {cap_mark} |")
    else:
        lines.append("*No ELITE managers found in this GW data.*")
    
    # SHARP managers
    lines.append("\n---\n")
    lines.append(f"## 🟠 SHARP Managers ({len(sharp_comps)})\n")
    if sharp_comps:
        # Top 20 SHARP by GW points
        sorted_sharp = sorted(sharp_comps, key=lambda c: (c.get('gw_points',0) or 0), reverse=True)
        lines.append("| # | Team | Manager | Score | GW Pts | Total | Captain | Transfers |")
        lines.append("|:-:|:-----|:--------|:-----:|:------:|:-----:|:--------|:---------:|")
        for i, comp in enumerate(sorted_sharp[:20], 1):
            sd = scout_data.get(comp['entry_id'], {})
            cap = comp.get('captain', 'N/A')
            lines.append(f"| {i} | {comp.get('entry_name','?')} | {comp.get('player_name','?')} | {sd.get('overall_score','?')} | {comp.get('gw_points',0)} | {comp.get('total_points',0)} | {cap} | {comp.get('transfers_made',0)} |")
    else:
        lines.append("*No SHARP managers found in this GW data.*")
    
    # Rising stars - top 20 overall
    lines.append("\n---\n")
    lines.append(f"## ⭐ Rising Stars — Top 100 Overall\n")
    lines.append("| # | Team | Manager | GW Pts | Total | Captain | Squad Cost | Transfers |")
    lines.append("|:-:|:-----|:--------|:------:|:-----:|:--------|:----------:|:---------:|")
    for i, comp in enumerate(top100[:20], 1):
        cap = comp.get('captain', 'N/A')
        lines.append(f"| {i} | {comp.get('entry_name','?')} | {comp.get('player_name','?')} | {comp.get('gw_points',0)} | {comp.get('total_points',0)} | {cap} | £{comp.get('squad_cost',0)}m | {comp.get('transfers_made',0)} |")
    
    return "\n".join(lines)

def generate_competitive_strategy(gw, analysis, competitors, output_dir):
    """Generate tactical advice based on league data."""
    lines = []
    lines.append(f"# Competitive Strategy — After GW{gw}\n")
    lines.append(f"> Strategic insights based on league-wide trends\n")
    
    # Template analysis
    ownership = analysis['squad_ownership']
    template = ownership.get('template_players', [])
    
    lines.append("## 🎯 League Template\n")
    if template:
        lines.append(f"The league template consists of **{len(template)} players** owned by >50% of teams:\n")
        for p in template:
            pct = ownership['ownership'].get(p, {}).get('percentage', 0)
            lines.append(f"- **{p}** ({pct}% owned)")
    else:
        lines.append("No single player is owned by >50% of teams — league is diverse.\n")
    
    # Top 5 most owned
    top5 = sorted(ownership['ownership'].items(), key=lambda x: -x[1]['percentage'])[:5]
    lines.append("\n### Top 5 Most Owned\n")
    for name, data in top5:
        lines.append(f"- {name} — {data['percentage']}%")
    
    # Captain trends
    lines.append("\n## 🎯 Captain Trends\n")
    if ownership['captain_choices']:
        top_cap = ownership['captain_choices'][0]
        lines.append(f"- **Most popular captain:** {top_cap['name']} ({top_cap['percentage']}% of teams)")
        if len(ownership['captain_choices']) > 1:
            second = ownership['captain_choices'][1]
            lines.append(f"- **Second most popular:** {second['name']} ({second['percentage']}% of teams)")
            diff = top_cap['percentage'] - second['percentage']
            if diff > 20:
                lines.append(f"- ⚠️ **Captain cyborg detected:** {top_cap['name']} is massively favoured ({diff}% gap)")
            elif diff < 5 and top_cap['percentage'] < 40:
                lines.append(f"- 🔄 **Split captaincy:** League is divided — differential captain could be a big swing")
    
    # Transfer trends
    lines.append("\n## 🔄 Transfer Trends\n")
    t = analysis['transfers']
    lines.append(f"- **{t['pct_transferred']}%** of teams made transfers")
    lines.append(f"- **{t['hit_takers']}** teams took a -4pt hit")
    lines.append(f"- **Top transfer IN:** {t['most_transferred_in'][0][0] if t['most_transferred_in'] else 'N/A'} ({t['most_transferred_in'][0][1]} teams)" if t['most_transferred_in'] else "- No significant transfer activity")
    lines.append(f"- **Top transfer OUT:** {t['most_transferred_out'][0][0] if t['most_transferred_out'] else 'N/A'} ({t['most_transferred_out'][0][1]} teams)" if t['most_transferred_out'] else "")
    
    # Formation trends
    lines.append("\n## 🏗️ Formation Trends\n")
    top_form = analysis['formations'][0] if analysis['formations'] else ('N/A', 0)
    lines.append(f"- **Most popular formation:** {top_form[0]} ({top_form[1]} teams, {round(top_form[1]/ownership['total_teams']*100,1)}%)")
    if len(analysis['formations']) > 1:
        lines.append(f"- **Second most popular:** {analysis['formations'][1][0]} ({analysis['formations'][1][1]} teams)")
    
    # Differential opportunities
    lines.append("\n## 🎲 Differential Opportunities\n")
    diffs = [d for d in analysis['differentials'] if d['ownership'] <= 5]
    if diffs:
        lines.append("Players owned by ≤5% of the league (big differential potential):\n")
        for d in diffs[:10]:
            lines.append(f"- **{d['name']}** — {d['ownership']}% owned ({d['total_owners']} teams)")
    else:
        lines.append("No significant differentials found this GW.\n")
    
    # Squad cost analysis
    lines.append("\n## 💰 Squad Cost Analysis\n")
    lines.append(f"- **League average:** £{ownership['avg_budget']}m")
    lines.append(f"- **Maximum:** £{max(c.get('squad_cost',0) for c in competitors)}m")
    lines.append(f"- **Minimum:** £{min(c.get('squad_cost',0) for c in competitors)}m")
    
    # Weaknesses to exploit
    lines.append("\n## ⚔️ Weaknesses to Exploit\n")
    lines.append("### Common Weaknesses in League Strategy:\n")
    
    # Check if captain choices are too concentrated
    if ownership['captain_choices'] and ownership['captain_choices'][0]['percentage'] > 50:
        lines.append(f"1. **Captaincy groupthink:** {ownership['captain_choices'][0]['percentage']}% captained {ownership['captain_choices'][0]['name']}. If they blank, differential captains gain massively.")
    
    # Check for template over-reliance
    if len(template) > 3:
        lines.append(f"2. **Template heavy:** {len(template)} players at >50% ownership means most teams are very similar. Punts on differentials can shoot up the ranks.")
    
    lines.append(f"3. **Injury exposure:** {analysis['injuries']['total_injured_instances']} total injured players — {analysis['injuries']['avg_injured_per_team']} per team average. Monitor who's holding injured players.")
    
    # Check transfer inactivity
    if t['pct_transferred'] < 50:
        lines.append(f"4. **Transfer inactivity:** Only {t['pct_transferred']}% made transfers — many teams may have dead squads or missed price changes.")
    
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description='FPL GW Analysis Report Generator')
    parser.add_argument('--gw', type=int, required=True, help='Gameweek number')
    parser.add_argument('--league', type=int, default=58005, help='League ID (default: 58005)')
    parser.add_argument('--data-dir', default=DATA_DIR)
    parser.add_argument('--output-dir', default=REPORTS_DIR)
    parser.add_argument('--compact', action='store_true', help='Use compact data only (faster, less detail)')
    args = parser.parse_args()

    gw = args.gw
    league_id = args.league
    data_dir = args.data_dir
    league_name = f"League {league_id}"
    output_dir = os.path.join(args.output_dir, f"GW{gw}")
    os.makedirs(output_dir, exist_ok=True)

    print(f"📊 Generating analysis for GW{gw} — League {league_id}...")

    # Load data with league-specific filenames
    if args.compact:
        data = load_compact_data(gw, league_id, data_dir)
        if not data:
            print(f"❌ Compact data not found (gw{gw}_league{league_id}_compact.json). Run fetch_gw_data.py first.")
            return 1
        competitors = data['competitors']
        competitors_full = None
    else:
        data = load_full_data(gw, league_id, data_dir)
        if not data:
            print(f"❌ Full data not found (gw{gw}_league{league_id}_data.json). Run fetch_gw_data.py first.")
            return 1
        competitors = data['competitors']
        competitors_full = competitors
    
    # Load previous scout data for tier info
    scout_data = load_previous_scout_data(data_dir)
    print(f"✅ Loaded {len(competitors)} competitors, {len(scout_data)} scout records")
    
    print("📈 Running analysis...")
    
    # Run all analyses
    squad_ownership_data = analyze_squad_ownership(competitors)
    differentials_data = analyze_differentials(competitors, squad_ownership_data['ownership'])
    top_performers = analyze_top_performers(competitors)
    bottom_performers = analyze_bottom_performers(competitors)
    transfers_data = analyze_transfers(competitors)
    injuries_data = analyze_injuries(competitors)
    team_clusters = analyze_team_clusters(competitors)
    formations_data = analyze_formation_distribution(competitors)

    # Chips used this GW (from history chips list or active_chip field)
    chips_counter = Counter()
    for c in competitors:
        ch = c.get('chips_used') or []
        if ch:
            for chip_ev in ch:
                chips_counter[chip_ev.get('name', '?')] += 1
        else:
            ac = c.get('active_chip', 'none')
            if ac and ac != 'none':
                chips_counter[ac] += 1
    chips_data = dict(chips_counter)

    analysis = {
        'squad_ownership': squad_ownership_data,
        'differentials': differentials_data,
        'top_performers': top_performers,
        'bottom_performers': bottom_performers,
        'transfers': transfers_data,
        'injuries': injuries_data,
        'team_clusters': team_clusters,
        'formations': formations_data,
        'chips': chips_data,
    }
    
    # Generate reports
    print("📝 Generating reports...")
    
    # Main report
    main_report = generate_markdown_report(gw, analysis, competitors, output_dir, league_id, league_name)
    main_path = os.path.join(output_dir, f"GW{gw}_L{league_id}_REPORT.md")
    with open(main_path, 'w') as f:
        f.write(main_report)
    print(f"✅ Main report: {main_path}")
    
    # Elite watch report
    elite_report = generate_elite_watch_report(gw, competitors, scout_data, output_dir)
    elite_path = os.path.join(output_dir, f"GW{gw}_L{league_id}_ELITE_WATCH.md")
    with open(elite_path, 'w') as f:
        f.write(elite_report)
    print(f"✅ Elite watch: {elite_path}")
    
    # Strategy report
    strategy_report = generate_competitive_strategy(gw, analysis, competitors, output_dir)
    strategy_path = os.path.join(output_dir, f"GW{gw}_L{league_id}_STRATEGY.md")
    with open(strategy_path, 'w') as f:
        f.write(strategy_report)
    print(f"✅ Strategy: {strategy_path}")
    
    # Save analysis JSON for programmatic use
    analysis_json_path = os.path.join(output_dir, f"GW{gw}_L{league_id}_analysis.json")
    # Make it JSON serializable
    analysis_serializable = {
        'gw': gw,
        'generated_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'total_competitors': len(competitors),
        'squad_ownership': {
            'total_teams': squad_ownership_data['total_teams'],
            'avg_budget': squad_ownership_data['avg_budget'],
            'avg_total_points': squad_ownership_data['avg_total_points'],
            'avg_gw_points': squad_ownership_data['avg_gw_points'],
            'max_gw_points': squad_ownership_data['max_gw_points'],
            'min_gw_points': squad_ownership_data['min_gw_points'],
            'template_players': squad_ownership_data['template_players'],
            'top_owned': [{'name': n, 'pct': d['percentage']} for n, d in sorted(squad_ownership_data['ownership'].items(), key=lambda x: -x[1]['percentage'])[:30]],
            'captain_choices': squad_ownership_data['captain_choices'][:10],
        },
        'transfers': {
            'total_transfers': transfers_data['total_transfers'],
            'teams_with_transfers': transfers_data['teams_with_transfers'],
            'hit_takers': transfers_data['hit_takers'],
            'top_transfers_in': [{'name': n, 'count': c} for n, c in transfers_data['most_transferred_in'][:10]],
            'top_transfers_out': [{'name': n, 'count': c} for n, c in transfers_data['most_transferred_out'][:10]],
        },
        'formations': [{'formation': f, 'count': c} for f, c in formations_data],
        'injuries': injuries_data,
        'chips': chips_data,
    }
    with open(analysis_json_path, 'w') as f:
        json.dump(analysis_serializable, f, indent=2)
    print(f"✅ Analysis JSON: {analysis_json_path}")

    print(f"\n🎉 All reports generated in {output_dir}/")
    return 0


if __name__ == '__main__':
    sys.exit(main())
