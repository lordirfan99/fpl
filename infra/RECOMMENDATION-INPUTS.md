# Current recommendation inputs

The public API selects fresh league captures and validates catalogue/fixture
capture times. Stale, invalid or missing required inputs hold recommendations,
including explicit historical gameweek pins. Public event picks and bank are
historical account evidence even when fetched seconds ago. The response states
`scope=league_research`, `account_state_verified=false`, and contains no personal
transfer/captain actions. Its league template remains available to the planner.

The VM planner requests the unpinned decision endpoint and validates the expected
public gameweek and a maximum 12-hour capture age. It separately reads
authenticated my-team picks, bank, selling prices and free transfers. Alignment
is recomputed from those current picks. Fresh API templates are not replaced by
an older local league-intelligence file. FPL execution still requires owner
approval through the existing bot; these changes add no execution path.

## Release

Merge only after green CI, create a release tag, and deploy the API from that
tag using `infra/cloudbuild.api.yaml`. The existing reviewed web workflow deploys
the same merged source. The public collector does not need reinstalling for this
change: its data schema is unchanged.

For the planner, clone the tag on the active VM in `us-central1-a`. Record
whether `fpl-auto-runner.timer` is active, then stop that timer. Let any running
planner finish. Run the following from the clean tagged checkout:

```bash
bash infra/deploy/install-recommendation-planner.sh <release-tag>
sudo -u fpl env FPL_AUTOPILOT_HOME=/opt/fpl-autopilot /opt/fpl-autopilot/.venv/bin/python /opt/fpl-autopilot/jobs/pre_deadline_run.py --verify-inputs-only
```

Require exit zero, verified account inputs and recent league evidence. Restore
the timer's prior active state even if verification fails (rollback first on
failure). The diagnostic never saves a pending plan or sends a card. The next
normal scheduled planning run uses the new inputs. No bot restart is needed.
The installer only replaces two planner files and backs up their originals under
`/var/backups/fpl-planner/<release-SHA>`. It never replaces bot units, credentials,
data or dependencies. Rollback from the same checkout, with the timer stopped:

```bash
bash infra/deploy/install-recommendation-planner.sh <release-tag> --rollback
```

Verify both leagues' recommendation and decision endpoints, the dashboard's
account-verification message, and the existing production monitor. A hold is an
honest degraded state, not proof of readiness to recommend an action. Keep load
test timeout/error thresholds intact; concurrent independent league-page reads
reduce avoidable waiting, but do not guarantee an external service never times out.
