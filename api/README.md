# Fantasy Scout API

Versioned FastAPI read service for the dashboard. Completed-gameweek analysis is read from
validated collector snapshots, while `GET /v1/live/team` polls the official FPL API through
a short server-side cache for the configured personal team. Live data is never used to rewrite
snapshots or journal records.

## Run locally

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8080
```

Run those commands from `services/api`.

## Safety boundary

The service intentionally exposes no transfer or captain write endpoints. Telegram approvals remain disabled until a stable HTTPS webhook, a verified Telegram user ID, and a webhook secret are configured.

## Data contracts

- `/v1/live/team` is mutable and provisional. It is suitable for the current Gameweek team view.
- `/v1/me/team`, `/v1/leagues/{league_id}`, `/v1/elite/{gw}` and recommendations remain
  snapshot-backed so rankings and cohort comparisons use one consistent capture.
- `/v1/leagues/{league_id}/decision-context` exposes only recorded public league facts. The
  top-10% cutoff requires complete membership, rival percentages state their hydrated sample,
  and missing historical captures remain `null` rather than becoming zero.
- `/v1/private/dashboard/current` requires the server-only read token and returns
  `private, no-store`; it never falls back to public exports or repository files.
- A completed Gameweek is promoted into the journal only after FPL marks it finished and
  data-checked. See [`docs/CURRENT_STATUS.md`](../../docs/CURRENT_STATUS.md).
