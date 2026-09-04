// Synthetic evidence for tests only. Never imported by application code.
export function packet() {
  const now = new Date().toISOString();
  const players = Array.from({ length: 16 }, (_, i) => ({
    id: i + 1, name: `Test Player ${i + 1}`, position: i === 0 || i === 11 ? "GKP" : i < 5 || i === 12 ? "DEF" : i < 9 || i === 13 ? "MID" : "FWD",
    club: i % 5 + 1, cost: 50, xpts: 5 + i / 10, xpts_by_gw: [5, 6, 4], expected_minutes: 75,
    facts: { team: i % 5 + 1, minutes: 180, starts: 2, expected_goals: "0.7", expected_assists: "0.3", defensive_contribution: 4, saves: 0, status: "a", news: "", news_added: null, chance_of_playing_next_round: null },
  }));
  return { schema_version: 1, team_id: 2797967, plan_id: "synthetic-test-plan", gameweek: 3, deadline: new Date(Date.now() + 3600000).toISOString(), generated_at: now,
    account_fingerprint: "synthetic-fingerprint", account: { picks: players.slice(0, 15).map((p, i) => ({ element: p.id, position: i + 1, selling_price: 50, is_captain: i === 5, is_vice_captain: i === 6 })), transfers: { bank: 10, limit: 2, made: 0, unlimited: false }, chips: [] },
    timestamps: { account: now, reference: now, league: now }, model_version: "test-model", chip: null, bank_after: 0.5, free_transfers_before: 2, free_transfers_after: 1,
    starters: [1,2,3,4,5,6,7,8,9,10,16], bench: [12,13,14,15], captain: 6, vice: 7,
    transfers: [{ element_out: 11, element_in: 16, out_name: "Test Player 11", in_name: "Test Player 16", hit: false, gain: 2, package_gain: 2 }],
    action: "TRANSFER", reason: "Synthetic recommendation: the legal package improves three-GW utility.",
    horizon: { metric: "risk-adjusted utility", rows: [3,4,5].map(gw => ({ gw, weight: 1, current: 50, proposed: 52, gain: 2 })) },
    alternatives: { hold: { horizon_gain: 0, net_after_hit: null, projection_starts_gw: 3, moves: [] } },
    captains: players.slice(5,8).map(p => ({ id: p.id, name: p.name, xpts: p.xpts, expected_minutes: 75, eligible: true, selected: p.id === 6, reason: "Reliable minutes and projected points" })),
    players, fixtures: [3,4,5].flatMap(gw => [{ id: gw * 2, event: gw, team_h: 1, team_a: 2, team_h_difficulty: 2, team_a_difficulty: 4, kickoff_time: now }, { id: gw * 2 + 1, event: gw, team_h: 3, team_a: 4, team_h_difficulty: 3, team_a_difficulty: 3, kickoff_time: now }]),
    teams: [1,2,3,4,5].map(id => ({ id, short_name: `T${id}` })), writes_enabled: false };
}
