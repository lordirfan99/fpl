# FPL Scout Intelligence V2 — clean rebuild specification

**Status:** implementation proposal only; NO production routing changes in this document.
**Source of truth:** `lordirfan99/fpl` main; existing `AGENTS.md` branch/PR/CI/tag rules.
**Non-negotiable:** preserve real official FPL evidence, frozen journal, private account
data, and Telegram owner approval guard. No live writes, fabricated rankings or
invented projections. Never convert an absent record to zero or a successful refresh.

## Why a rebuild

October 14 production HTML 200 route checks did not test data quality, browser
runtime, navigation, search, or stale/missing GW states. The live public web
migrated to GitHub Pages but old server-bound adapters, numerous backtracking
requests, SSR layouts, a separate streaming private route and frontend mock
fixtures still coexist. Existing production monitor [run 38560662784]
(https://github.com/lordirfan99/fpl/actions/runs/38560662784) failed with
HTTP 404 on the *legacy* VM `/v1/leagues/58005/summary`, showing observability
is not aligned with the active public data source. The first live browser audit
is [PR #143](https://github.com/lordirfan99/fpl/pull/143): use its artifacts,
not the earlier HTTP smoke alone, for actual screen-level defects.

## Core product: make 5 jobs work before adding features

| Priority | V2 destination | Minimum true outcome |
|---|---|---|
| P0 | Today | Last verified snapshot, current FPL deadline, source/age, verified personal decision or explicitly *locked/unavailable*. |
| P0 | My Team | All 15 official recorded picks, XI/bench/captain/vice, no phantom player photos or invalid prices, clear GW identity. |
| P0 | League | Both tracked leagues, exact archive weeks, search/pagination/manager dialog, no 404 surfacing as generic page crash. |
| P0 | Elite | Top 5% cohort derived from a *complete* league, formation, squad, captains and differentials with denominator. |
| P0 | Players | Fast searchable/filterable player market, injuries, prices, fixtures, source timestamp; no false recommendation from stale estimates. |
| P1 | Compare | Two selected managers from same league/GW, validated 15-player squads, deterministic URL state. |
| P1 | Planner | Five-GW fixture view, public research vs authenticated plan visibly separated; no executable plan without verified account. |
| P1 | Journal | GW history with immutable hashes, explicit archival gaps, exact timestamps and CSV export. |
| P2 | Labs/analytics/advanced | Reintroduce only after P0/P1 acceptance gates succeed, with a documented purpose per feature. |

## Architecture

```
 Official FPL public endpoints
              |
    scheduled standard GitHub Actions collector (pinned reviewed release)
              |
 Validation: both leagues COMPLETE, schema, snapshot provenance, immutable archives
              |
   GitHub Pages versioned manifest + content-addressed files
              |
    V2 PublicDataGateway (ONE typed adapter, bounded fetch + integrity checks)
              |
       Read-only Next.js V2 screens (Netlify)
              |
   ┌─────────────────────────────────────────────────────────┐
   │ Private account / plan zone (separate authenticated API)│
   │ Existing GCP VM: planner, verified packet & Telegram bot│
   │ Bot may write only after explicit owner approval of hash │
   └─────────────────────────────────────────────────────────┘
```

**No per-page direct VM fallback** for public data and no mixing finalized/current
snapshots without an explicit user-selectable source label. Private endpoints
may use the existing VM only via authenticated, no-store server paths; never
include tokens, prices, private plans, account fingerprints or session data in
GitHub Pages. Public `writes_enabled` stays false. FPL website never auto-writes.

### Public contract: explicit states, never ambiguous success

Every screen resolves an explicit snapshot state:
`ready`, `stale`, `not_published`, `incomplete`, `unreachable`,
`integrity_failed` (no phantom zero values). Include:
`season`, `league_id`, `requested_gw`, `resolved_gw`,
`is_finalized`, `captured_at`, `age_hours`, `source`,
`count`, `expected_count`, `coverage`, `last_good_release`.
If missing GW, show “GW N unavailable” and an *explicit link* to latest
finalized GW; never silently change the user's requested GW. No bogus
“live” label on delayed hourly captures.

Keep archived League 131997 GW2/3/5/6 unavailable until real immutable
snapshots can be imported and verified (as recorded during Oct 14 cutover).
A missing record is an **empty state, not a page failure**.

### Clean boundaries

- `web/v2/lib/public-data`: schema guards, manifest verification, GW availability
  resolution, small bounded server cache, request coalescing, error mapping.
- `web/v2/lib/private-plan`: server-only verified packet; no cache; explicit auth.
- `web/v2/components`: accessible navigation, error/empty/skeleton states and
  component primitives; mobile and desktop share routing semantics.
- `web/app/v2/*`: new isolated route group; do not replace original URLs yet.
- Public data is fetched once per relevant screen; no 38-GW fetch-on-navigation
  loops, no full league transfer when only a directory/summary is needed.
- No dynamic SSR layout fetch that can block *every* route if the catalog is
  temporarily unavailable. Shell/navigation must render without a live API.

## User experience and performance

A calm professional UI, minimal top navigation, clear active league/GW selector,
no page-specific 38-link clutter. Use recognizable FPL position/club badges and
readable mobile grids. Preserve the last visible page while changing filters;
show request progress; never leave an infinite skeleton or overlay.
Desktop sidebar and mobile bottom navigation must match.

Performance budgets for public pages (from **real browser CI**, measured after
warm-up): meaningful heading under 2 seconds, 95th percentile route interaction
under 4 seconds on production reference network, and no page-blocking request
to the VM. Report slower runs honestly rather than weakening the gate.

## Release acceptance matrix — test the REAL site

Run on phone viewport (390x844) and desktop (1365x900), no authentication:
- All P0/P1 routes load with expected main heading *and useful content or a
  truthful empty state*, not just HTTP 200.
- Click every visible top/bottom navigation link and “More”; check current
  route, hidden/visible drawer, back behavior, keyboard Escape.
- Switch 58005 ↔ 131997 on League, Elite, Compare and Today; ensure
  count/source/heading update.
- Search manager ID/name, page forward/back, open & close lineup.
- Select current GW, a finalized archived GW, an unavailable GW,
  and an invalid GW; confirm exact/empty state with no crash or invented data.
- Test compare selectors (same ID disallowed; two different valid managers).
- Search/filter players, reset filters and empty results.
- Verify all links and buttons are actionable; no console exceptions,
  hydration errors, unexpected 4xx/5xx or hanging fetch.
- Simulate Pages down, checksum mismatch, stale catalog, incomplete league,
  missing private packet and signed-out auth; correct failures stay isolated.
- Never treat a 404 from the *legacy* VM as public website status.
- Verify secured personal plan via separate permitted authenticated test,
  without storing credentials or leaking private responses in CI artifacts.
- Use source-captured timestamp, never job runtime, for freshness.

Each PR must include automated tests. Actual production release only after
both CI jobs are green and the new real-browser suite passes on a production
preview, plus a pre-release verification checklist.

## Staged delivery (avoid another broken big-bang)

0. **Baseline:** PR #143 real browser/console/network inventory, root-cause
   log for every failed page and repeatable artifact; no UI rewrite yet.
1. **Foundation:** new isolated V2 shell/navigation, typed data gateway,
   global failures/empty states, Pages manifest integration, unit tests.
2. **Five core screens:** Today, My Team, League, Elite, Players.
   Prove mobile/desktop use cases and both leagues with actual dataset.
3. **Decision extras:** Compare, Planner, Journal. Preserve the private plan
   security boundary and hash-approved Telegram workflow.
4. **Release:** deploy V2 to preview/shadow URL; screenshot diff and play
   through all routes. Only then switch canonical production routing with
   tagged rollback plan. Keep V1 immutable until V2 is verified.
5. **Cleanup:** remove redundant VM public monitor/load, obsolete adapters and
   stale routes in separate reviewed PRs. Preserve data and rollback archive.

**Definition of done:** no broken tab across matrix, real source-aware data
on all P0/P1 screens, working interactions, documented missing data, passing
production browser verification, healthy hourly publication, rollback tested.

## Explicit exclusions

No new paid infrastructure, no FPL writes, no unapproved account operations,
no destructive history migration, no forced repo reset, no deployment from an
unreviewed branch or a failed CI. Feature additions and visual restyling are
secondary to functionality and trustworthiness.
