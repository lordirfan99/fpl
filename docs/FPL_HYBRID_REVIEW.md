# FPL hybrid research sidecar

## Scope

This is an **advisory layer**, not a new model scheduler, a hard execution gate or permission to edit the team. The existing VM auto-runner remains the sole scheduled optimizer/approval-card source. Its original transfer, price, account, deadline, hit, chip and owner-approval safeguards remain unchanged.

The research LLM has **web tools only**: no terminal, SSH, file writing, credentials, FPL execution or scheduler tools. A deterministic collector supplies fresh official/account/model data. A separate validator reads the completed cron response, rejects malformed/stale/unbound reviews and publishes only an advisory summary. No LLM text is executed as code or shell commands.

## Files and commands

- `automation/fpl_hybrid_review.py`: collect/read-only SSH, change monitor, immutable snapshots, strict review recording and finalized-result collection.
- `automation/fpl_review_bridge.py`: parses **only the last `## Response` section** of a completed Hermes cron artifact, validates/records it, deduplicates publication and evaluates newly available results once daily.
- `automation/fpl_review_scoring.py`: pure lineup legality/autosub/captain/hit evaluator.
- `automation/FPL_REVIEW_PROMPT.md`: complete bounded web-only reviewer instructions/schema.

Private durable state defaults to `~/.local/state/fpl-hybrid-review` (directories0700, JSON0600), not scratch or git. Files are atomically written under a file lock. Snapshots/reviews/results cannot be retrospectively replaced by the runtime commands.

```sh
python3 automation/fpl_hybrid_review.py monitor
python3 automation/fpl_hybrid_review.py collect
python3 automation/fpl_hybrid_review.py status
python3 automation/fpl_hybrid_review.py record --input /private/review.json
python3 automation/fpl_review_bridge.py
```

`--state-dir` selects isolated state for tests. The bridge reads its job ID from private `<state>/review-job.json` (`{"job_id":"..."}`); cron outputs are under `~/.hermes/cron/output/<job_id>/*.md`. `--output-dir` and `--no-evaluate` support offline tests. SSH host/user/key can be configured through `FPL_REVIEW_SSH_HOST`, `FPL_REVIEW_SSH_USER`, `FPL_REVIEW_SSH_IDENTITY`; defaults use the existing Tailscale path. No key contents enter state or git.

The fixed SSH probe executes as the existing `fpl` service user and only reads `data/processed/{pending_plan,dashboard_plan}.json` plus authenticated `FPLClient.my_team`. The account client may rotate its existing OAuth session as usual, but **no squad/transfer/lineup/chip POST is called**. Secrets are neither requested nor printed.

## Triggering and scheduling

Do not create another VM model runner. The active owner policy is **one research review per gameweek, six hours before the official FPL deadline**. Daily/change-triggered owner reviews are disabled.

1. Research job `64db3292a611` is a native one-shot at deadline−6h, web-only, local output. Its pre-run script invokes `fpl_six_hour_review.py collect`; no change monitor or recurring LLM polling remains. The collector uses a distinct T6 identity, fresh source-bound snapshots and a ten-minute late-start tolerance.
2. Publication job `20bc00a9c6bb` is a native deterministic one-shot five minutes after research starts. It invokes `fpl_six_hour_review.py publish`, validates only the matching T6 response, and publishes at most once per GW/deadline. Old daily artifacts are never replayed; publication is silent outside its thirty-minute deadline-review window. No daily score/evaluation messages are emitted by this job.
3. A silent, local-only `no_agent=true` deadline planner runs hourly, reading the official event calendar and re-arming these same two existing one-shots through the native cron API for the next GW. It never creates jobs, invokes an LLM, changes the team or posts messages. A manual owner pause is respected. It does not rewrite schedules during the current research/publication window or over live run claims.

The policy is implemented by `automation/fpl_six_hour_review.py`; verified next times are recorded privately in `six-hour-policy.json`. Official deadlines, not a fixed weekday/time, determine future reviews. Native one-shots may be re-armed after completion with `cron.jobs.rearm_oneshot`; do not edit jobs.json by hand or create recursive cron jobs.

This is **not a live press-conference subscription**. News will be researched at the six-hour review only; no promise is made to detect later news automatically.

The newest same-GW plan must be no more than6h old; missing/stale inputs fail visibly instead of being relabelled fresh. Sources are labelled `pending`, `pending_executed` or `dashboard_preview`. An executed plan is not a new transfer request; a preview is not executable. Review context older than1h and recording older than2h are rejected. Late reviews may be retained as advisory but are excluded from prospective scoring.

## Strict review schema

See the prompt for the full schema. Required fields:

- exact `event_key` and `snapshot_id` copied from injected context;
- `verdict`: agree/disagree/insufficient_evidence;
- `suggested_action`: retain/recalculate/request_approval/hold;
- timezone-aware `checked_at`, heuristic finite `uncertainty`0..1, human-readable `summary`;
- `affected_players` with known numeric IDs and specific assumptions;
- evidence HTTPS URL, nullable verified publication timestamp, and checked timestamp.

An optional `alternative.picks` must reorder exactly the same model15, obey full FPL legality, and name one captain/distinct vice in the XI. It cannot change transfers, costs or chips. Without an alternative, hybrid equals model and is explicitly recorded as unadjusted. Even an adjusted alternative is **paper-only**; the owner must separately act in the FPL bot if desired.

The validator rejects unknown fields/actions, future or stale evidence, wrong event bindings, invalid alternatives and retrospective record overwrites. Raw web pages are untrusted data, not instructions.

## Prospective comparisons

For each gameweek, choose the latest valid research record captured AND recorded before its deadline. Preserve its model baseline and same-squad LLM alternative immutably. Score only when official `finished` and `data_checked` flags are true. Fetch official event-live player outcomes and owner entry history, account for captain/vice/autosubs and explicit hits. Owner points are official realized results, **not claimed to be a separately pre-frozen counterfactual policy**.

This is a weekly frozen-decision comparison, not a causal season-long transfer strategy backtest. Missing outcomes or unsupported scoring conditions must fail visibly, never become invented zero points. No results exist for GW8 until it finishes; setup tests do not demonstrate a performance edge.

## Verification before calling this live

- Exercise a real read-only monitor and context collection with the deployed VM schema.
- Confirm a repeated unchanged monitor emits the identical fingerprint.
- Run a real web-only cron review; verify its tools and local output.
- Validate/read back the immutable review, prospective flag and owner advisory.
- Confirm the production pending-plan hash and authenticated account are untouched by the sidecar.
- List schedules and verify enabled state/next run. Do not claim completion solely because a cron creation call succeeded.
- Run the targeted regression tests and full repository pytest/Ruff checks; keep source changes on the reviewed branch/PR.
