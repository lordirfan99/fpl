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
- Public GCS reads now have a 3-second wall-clock wait, including SDK credential
  refresh and TLS, with retries disabled. A fixed pool admits at most two remote
  operations and has no queue. Timed-out workers cannot mutate caches; a hung
  operation occupies its slot until it returns (restart the tagged service if
  both remain stuck). Artifact coalescing waits at most three seconds. Live
  manifest plus payload is one remote operation; decision manifest and evidence
  each have the same bound. These limits do not guarantee overall readiness
  latency: `/ready` still validates multiple artifacts.
- Remote public payloads use a four-entry LRU, local JSON reads retain at most
  four entries, and the hash memo retains at most two payloads. Live/decision
  caches retain two entries each. These are entry limits, not measured byte
  limits; verify RSS under normal collection. Failed remote verification clears
  previous data and provenance; bounded negative caching avoids retry storms.
- `/v1/leagues/{id}/compact` and `/live/compact` return every manager and every
  recorded squad. Shared player metadata is encoded once; each squad references
  that pool while preserving its order, price, multiplier and captain flags.
  Transfer/chip names and previous rank remain available. Collector history and
  unused upstream response objects are omitted. Source timestamps, checksums,
  population coverage and gameweek remain unchanged. Original full routes stay
  compatible; dashboard research now uses compact routes.
- The live collector publishes public bootstrap and fixture references before
  collecting leagues, so a failed long league run does not prevent reference
  publication. Bootstrap capture metadata/hash and fixture source time are
  included. This does not publish sampled planner cohorts as complete leagues
  or generate missing historical snapshots. Missing public artifacts return 503.
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
- Private account/session/decision upstream reads still abort after eight seconds,
  including response bodies, and remain no-store. The weekly client retains its
  ten-second overall deadline and independent section recovery.
- Public captured research (catalog, league snapshots, journal and model
  projections) uses an eight-second body-inclusive cold-read timeout. API research
  responses use an eight-entry
  warm-instance LRU cache with coalesced concurrent reads; failed responses are
  not cached. Capture timestamps are never renewed by a cache hit. Only a live
  status with ready=true may override the catalog gameweek.
- Navigation, league switches, journal links and the season rail disable automatic
  prefetch. Navigation/Players/Journal use the compact catalog; live identity
  comes from compact live-status, not a full personal-team request. The dashboard
  remains request-time rendered with force-dynamic; the framework fetch cache
  may be bypassed, so the bounded warm-instance cache provides reuse. These
  caches contain public records only; private account packets remain isolated.
- PuLP is constrained below major version 4 because the engine uses the existing
  `LpVariable(..., cat=...)` and CBC APIs. New installs otherwise break lineup and
  horizon optimization. This does not change the scoring model.

## Rollout and rollback

1. Review the PR and verify both CI jobs are green. Record the current release
   and which collector/planning timers are active. Use existing read-only VM
   service/resource logs to distinguish application latency from VM memory,
   thread or network saturation before changing scheduling.
2. Create a reviewed release tag WITHOUT triggering the Netlify tag workflow
   until the API below is verified (use a GitHub-token-created tag, which does
   not recursively trigger workflows, or the existing manual release controls).
   From a clean checkout of that same tag on the VM, pause the units required by
   `infra/deploy/install-live-read-cache.sh`; confirm no collector/planner job is
   running. Use that immutable installer with the new tag. It installs the same
   collector and API release and preserves the existing environment values.
   Follow its rollback procedure if readiness fails. Do not run the planner or
   approval bot as part of a dashboard release.
3. Verify BOTH new compact routes before publishing that tag to Netlify. The
   website requires the new API protocol; do not publish the website first.
   Restore only previously active timers after API readiness. Wait for the next
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


## Remaining operational gate

The October 14 report confirms OOM, frequent collector timeout and planner overlap
on the 1 GiB VM. This patch reduces request pressure and supplies missing public
references; it does not resize the VM or change scheduler/write behavior. Verify
one complete ordinary collector cycle with no OOM/502, stable RSS and responsive
liveness before calling recovery complete. If collector/planner overlap still
causes starvation, prepare a separate reviewed shared-lock/scheduler change.
If references are absent before installation, use the reviewed reference publisher
from the clean tagged checkout as user `fpl`, with its existing collector bucket
identity; it must not call the planner, bot, or full league collector. No credentials
or public datasets belong in git. Resume only previously active timers.
