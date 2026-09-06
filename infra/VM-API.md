# VM-hosted read API

The read-only FastAPI service runs on the existing free-tier VM. Caddy exposes
it at `https://sportmania.duckdns.org/fpl-scout-api/`; uvicorn listens only on
`127.0.0.1:8790`. No Cloud Run service or job is required.

## Release and install

Merge only after CI is green, tag `main`, and use a clean checkout of that tag
on the active VM. First install the same API source and isolated virtual
environment used by the VM live collector:

```bash
sudo systemctl stop fpl-live-refresh.timer
sudo systemctl stop fpl-live-refresh.service
bash infra/deploy/install-live-refresh.sh <tag>
```

Prepare `/tmp/fpl-scout-api.env` outside git with mode 0600. It must contain:

```text
FPL_MY_TEAM_ID=2797967
FPL_DEFAULT_LEAGUE_ID=58005
FPL_SNAPSHOT_BUCKET=irfan-374115-fpl-snapshots
FPL_PRIVATE_DASHBOARD_BUCKET=irfan-374115-fpl-private-dashboard
FPL_DASHBOARD_READ_TOKEN=<Secret Manager value>
FPL_GIT_SHA=<full tagged commit>
FPL_BUILD_TIME=<UTC timestamp>
FPL_ALLOWED_ORIGINS=https://fpl-scout-intelligence.netlify.app
FPL_DATA_DIR=/opt/fpl-scout-api/data
```

Then run the tagged installer. It verifies the environment, requires the live
collector release to match the tag, backs up Caddy/systemd state, validates
Caddy before reload, starts a single resource-bounded worker, and checks local
and public health routes.

```bash
bash infra/deploy/install-vm-api.sh <tag> install /tmp/fpl-scout-api.env
sudo rm -f -- /tmp/fpl-scout-api.env
sudo systemctl start fpl-live-refresh.timer
```

Set both Netlify production `FPL_API_BASE_URL` and the GitHub repository variable
to `https://sportmania.duckdns.org/fpl-scout-api`, redeploy Netlify, and run the
full production monitor plus phone/private-login checks. Delete Cloud Run only
after endpoint parity and authenticated private access pass.

## Rollback

From the same clean tagged checkout:

```bash
bash infra/deploy/install-vm-api.sh <tag> --rollback
```

Restore the previous Netlify and GitHub API URL before retiring the VM service.
The installer restores the exact prior Caddyfile, environment file and systemd
unit. Release directories and GCS data remain for diagnosis.
