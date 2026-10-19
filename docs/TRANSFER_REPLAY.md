# Dynamic transfer replay

`engine/jobs/run_transfer_replay.py` is a read-only, chronological replay for GW3–GW6.
It starts with the actual GW2 squad, £1.0m bank and 142 points, then applies the
fixed policy:

- GW3 wildcard is a full rebuild using the archived GW3 pre-deadline catalog.
- GW4–GW6 allow at most one free transfer per week, no hits, and no banked-free-
  transfer accumulation.
- Every decision uses only event history before the target GW. Players absent from
  history receive the fixed positional prior in the output manifest; no scored
  actuals are used for that fallback.
- New purchases use their archived catalog price. Retained players keep purchase
  price; sale value is `purchase + floor((current - purchase)/2)` for gains and
  current market price otherwise.
- Catalog candidates must be available (`status == a`, chance not zero). A player
  already held but subsequently unavailable may be retained, but cannot be picked
  in the XI. Legal squads are 2 GKP / 5 DEF / 5 MID / 3 FWD, max three per club;
  XI, autosub, captain and vice-captain are scored from the event history.

The public picks payload lacks a purchase ledger. When supplied, the optional
`initial_purchase_ledger.json` reconstructs initial purchases from GW1 archived
prices plus official GW2 transfer prices, and checks the GW2 squad identity.
On this dataset both that ledger and a GW3 market-value sensitivity yield a
£99.7m wildcard budget. Earlier preseason purchase-price differences remain
unverified. Without the optional ledger, the replay uses GW3 market valuation.
Capture filenames/save timestamps are operational provenance rather than
cryptographic proof of deadline capture. GW7 is excluded because it is unfinished.
The replay is retrospective development research, not an untouched holdout.

Parent-reviewed rerun: two-stage Bayes scores 34, 73, 71, 51 in GW3–6;
142 unchanged warmup points + 229 replay points = 371 through GW6, versus
340 actual. Ridge scores 357 total; archived `ep_next` scores 330. The +100
request was not achieved. Neither the historical best-model selection nor the
four-week sample establishes a top-five-percent finish.

## Reproduce

```bash
python3 engine/jobs/run_transfer_replay.py \
  --history /home/ubuntu/.hermes/cache/scratch/fpl-revamp/input/player_event_history.jsonl \
  --catalog-dir /home/ubuntu/.hermes/cache/scratch/fpl-revamp/transfer-replay/input \
  --gw2-picks /home/ubuntu/.hermes/cache/scratch/fpl-revamp/transfer-replay/input/gw2_picks.json \
  --official-history /home/ubuntu/.hermes/cache/scratch/fpl-revamp/points-backtest/entry_2797967_history_.json \
  --output /home/ubuntu/.hermes/cache/scratch/fpl-revamp/transfer-replay/result.json
```

The output includes `two_stage_bayes` (primary), `ridge` (comparator), and
`ep_next` (archived official projection comparator), per-GW squads, transfers,
bank, captain, autosubs, cumulative GW6 points, catalog hashes, and provenance
limits. It does not reuse the tournament's hardcoded oracle ceiling.
