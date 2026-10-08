# Bounded dashboard reads and compact league evidence

This release changes the collector, API and Next.js dashboard together. Follow
AGENTS.md: branch, PR, green CI, review, tagged release; never edit the running VM.
No FPL account write, Telegram notification, or optimizer refresh is part of this
rollout.

## Behavior

- The existing live collector uploads `*-decision.json` before advancing each
  league's current manifest. It contains only recorded public league facts,
  ownership coverage, source time and the configured owner's public entry ID.
  The manifest binds it by SHA-256; it also records the full snapshot hash.
- `/v1/leagues/{id}/decision-context?include_history=false` reads that small
  artifact, checks checksum, league, owner, gameweek, source time and snapshot
  identity, and recomputes freshness at response time. It never downloads full
  squads when compact evidence is present. Old manifests without compact fields
  retain the existing full-snapshot path, without history, during rollout.
  Present-but-invalid compact evidence fails closed; it cannot silently use a
  different capture. Default historical requests remain compatible.
- General reference and compact storage calls use 3-second per-operation
  timeouts with retries disabled. Existing larger full-live reads retain their
  explicit 15/20-second limits. Compact cache revalidation is 30 seconds and
  bounded to two leagues. Do not interpret per-operation limits as a guaranteed
  overall readiness deadline: `/ready` still validates multiple artifacts.
- Private dashboard reads reuse the storage client, check private-bucket
  protection once per pair, coalesce concurrent reads, and cache the pair for at
  most five seconds. Each storage operation is bounded to two seconds, retries
  are disabled, and lock waits are bounded to one second. Failed protection or
  reads discard previous data. Every response still validates owner, account
  fingerprint, check age, plan identity, source age and deadline. The cache can
  delay a new account invalidation by at most five seconds; it never renews the
  account-check timestamp or extends its 20-minute expiry.
- `/livez` performs no storage reads. `/health` and `/ready` retain the existing
  readiness contract. Installation checks liveness first, then full readiness.
- Dashboard upstream reads abort after eight seconds including response bodies.
  The weekly client has a ten-second overall deadline, keeps independently
  received league evidence, and offers explicit retries without automatic retry
  storms. Navigation and Players use only the compact catalog; rendered
  reference requests are deduplicated per render, not cached across owners.
- PuLP is constrained below major version 4 because the engine uses the existing
  `LpVariable(..., cat=...)` and CBC APIs. New installs otherwise break lineup and
  horizon optimization. This does not change the scoring model.

## Rollout and rollback

1. Review the PR and verify both CI jobs are green. Record the current release
   and which collector/planning timers are active. Use existing read-only VM
   service/resource logs to distinguish application latency from VM memory,
   thread or network saturation before changing scheduling.
2. Create a reviewed release tag. The existing tag workflow publishes Netlify.
   From a clean checkout of that same tag on the VM, pause the units required by
   `infra/deploy/install-live-read-cache.sh`; confirm no collector/planner job is
   running. Use that immutable installer with the new tag. It installs the same
   collector and API release and preserves the existing environment values.
   Follow its rollback procedure if readiness fails. Do not run the planner or
   approval bot as part of a dashboard release.
3. Restore only previously active timers after API readiness. Wait for the next
   successful normal collector capture for BOTH leagues. Inspect manifest field
   names and checksums without printing manager squads, tokens or private plans.
   Confirm each has `decision_object` and `decision_sha256` tied to its current
   `snapshot_sha256` before claiming the fast cold overview path is deployed.
4. Verify anonymous overview/private 401 behavior, owner sign-in, full streamed
   section delivery, HOLD checklist, captain/bench/bank, player search, source
   timestamps, coverage and focus revalidation. Test an upstream timeout: the
   plan must disappear rather than become HOLD. Measure complete sections and
   response bodies, not only HTTP 200 headers. Check both cold and warm requests
   while normal collection runs; report actual latency, not a promised SLA.
5. Update `docs/RUNBOOK.md` with the tag, deployed revisions and measured results.
   Local fixtures/CI passing do not establish production VM health or model-input
   correctness. No VM credentials were available in the initial repair session.

Rollback uses `install-live-read-cache.sh <new-tag> --rollback` from the clean new
release checkout, plus the previous Netlify deployment. Restore only the prior
active timers after verification. The extra compact artifacts are additive and
safe for old API/collector code; retain immutable captures and their manifests.
