# FPL hybrid review sidecar

`automation/fpl_hybrid_review.py` is an advisory-only sidecar. It does not import the
FPL execution client and has no transfer, lineup, captain, chip, or model-weight
mutation path.

## Commands

Use a private state directory (the default is
`~/.local/state/fpl-hybrid-review`, mode 0700; JSON and lock files are mode 0600).
The offline form is useful for a cron wrapper that has already captured the
read-only VM response:

```sh
python automation/fpl_hybrid_review.py monitor --gameweek 8 --input /private/probe.json
python automation/fpl_hybrid_review.py collect --gameweek 8 --input /private/probe.json
python automation/fpl_hybrid_review.py status
python automation/fpl_hybrid_review.py record --input /private/review.json
python automation/fpl_hybrid_review.py evaluate --input /private/finished-gw.json
```

The probe JSON contains only `pending_plan.json`, `dashboard_plan.json`, and a
fresh `account` response. `pending_plan.json` is preferred and is labelled
`pending` (or `pending_executed`); dashboard-only data is labelled
`dashboard_preview`. Same-gameweek and exact plan-id binding is required.

For a live read-only capture, a wrapper may call `remote_probe(entry)` and
`public_json()` from the module. SSH configuration is environment-only:

* `FPL_REVIEW_SSH_HOST`
* `FPL_REVIEW_SSH_USER`
* `FPL_REVIEW_SSH_IDENTITY`
* `FPL_REVIEW_ENTRY`

The fixed remote command runs `/opt/fpl-autopilot/.venv/bin/python -` and reads
only the two plan files plus `FPLClient.my_team`. It never reads
`credentials.env`/session values and never invokes an execution method.

## Review contract

`record` accepts strict JSON containing the exact `event_key` and `snapshot_id`
from `status`, a verdict (`agree`, `disagree`, `insufficient_evidence`), a
suggestion (`retain`, `recalculate`, `request_approval`, `hold`), uncertainty
0..1, affected numeric player IDs, and evidence entries with URL,
`published_at` (nullable for explicit unknown), and `checked_at`. Reviews are
immutable. An optional alternative must contain the same model player IDs (no
fabricated transfers); absent alternatives mean the hybrid remains identical to
the model and is recorded as no adjustment.

Fingerprints omit timestamps and private credential fields, so unchanged checks
are silent at the caller level. Meaningful plan/account/public changes and the
24h/2h/1h deadline windows produce events. A failed source read raises a visible
error and does not reuse an earlier snapshot.

## Evaluation boundary

`evaluate` accepts only finished, `data_checked` official outcomes. The pure
scorer handles starter/bench order, legal formation autosubs, captain/vice
fallback, goalkeeper substitution, blanks (zero official points), doubles (the
official points supplied for the gameweek), and explicit hit costs. It refuses
missing/future data and requires an explicitly official owner actual. Results
are weekly decision comparisons, not a season-long causal transfer-policy
backtest.

Run targeted tests with:

```sh
python -m pytest -q engine/tests/test_fpl_hybrid_review.py
```

## Cron bridge schema

The web-only LLM job receives the JSON printed under `research_material` by
`collect`. The bridge must read the cron artifact's **last** `## Response`
section (the file is `~/.hermes/cron/output/<job_id>/YYYY-MM-DD_HH-MM-SS.md`),
then accept only strict JSON or one exact JSON code fence. It must not search for
the first brace in the whole artifact because the prompt can contain examples.
The response shape is:

```json
{
  "event_key": "gw8:p8:1deaadf30165a73a",
  "snapshot_id": "sha256...",
  "verdict": "agree",
  "suggested_action": "retain",
  "uncertainty": 0.2,
  "affected_players": [{"id": 9, "assumption": "news is actionable"}],
  "evidence": [{
    "url": "https://example.test/article",
    "published_at": null,
    "checked_at": "2026-10-20T00:00:00Z"
  }]
}
```

`event_key` and `snapshot_id` must be copied exactly from the context; the
bridge writes the response to a private JSON file and invokes `record --input`
only after deterministic validation. Context is a point-in-time snapshot and
must not be reused after a later monitor event; failed or stale source reads
are visible errors, not permission to reuse the previous context. The sidecar
does not create a scheduler, and it is advisory rather than an execution gate.
