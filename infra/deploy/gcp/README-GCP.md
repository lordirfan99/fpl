# FPL Autopilot — GCP Deployment Runbook

Deploy the FPL Autopilot system (data pull → xPts → MILP optimizer → Telegram
approval bot) on a Google Cloud VM. Written so an operator or AI assistant with
gcloud access can execute it end-to-end.

**Authoritative source of system internals:** `README.md` (repo root) and
`HERMES_HANDOFF.md`. Read both before touching anything.

---

## 0. Big picture

| Component | Local (Windows) equivalent | GCP equivalent |
|---|---|---|
| Telegram bot (polling, approval flow) | Task Scheduler + VBScript | `fpl-telegram.service` (systemd) |
| Data pull every 4h | Hermes cron `fpl-daily-pull` | `fpl-daily-pull.timer` |
| Pre-deadline + post-GW auto-runner every 2h | Hermes cron `fpl-auto-runner` | `fpl-auto-runner.timer` |
| OIDC token keepalive every 2h | Hermes cron `fpl-token-keepalive` | `fpl-token-keepalive.timer` |
| Auth canary 1st+15th 04:00 | Hermes cron `fpl-auth-canary` | `fpl-auth-canary.timer` |
| Historical odds research | Manual/offline only | `fpl-odds-fetch.timer` disabled |
| Squad watch daily 09:00 | Hermes cron `fpl-daily-squad-watch` | `fpl-squad-watch.timer` |
| Approval reminder every 30m | Hermes cron `fpl-approval-reminder` | `fpl-approval-reminder.timer` |
| Bot restart if heartbeat stale | Hermes cron `fpl-bot-watchdog` | `fpl-bot-watchdog.timer` |
| Deadline registry/picks freeze | none | `fpl-league-finalizer.timer` (10m no-op checks; work only in post-deadline window) |

Alert delivery: on Windows, Hermes delivered cron stdout to Discord/Telegram.
On GCP, every job pipes through `jobs/deliver_stdout.py` → non-empty output is
forwarded to the configured Telegram chat (`settings.json → telegram.chat_id`).
Empty output = silence. Same semantics as the old no_agent crons.

**Runtime user:** dedicated `fpl` system user. **Install root:** `/opt/fpl-autopilot`.

---

## 1. Prerequisites

1. Google Cloud project `irfan-374115`.
2. `gcloud` CLI authenticated as an owner/editor of that project.
3. Access to the private repo `github.com/lordirfan99/fpl-autopilot`
   (GitHub token or SSH key on the VM).
4. `config/credentials.env` + `config/fpl_session.json` present in the repo.

---

## 2. Production target (do not provision another VM)

The existing production VM is authoritative:

| Setting | Value |
|---|---|
| Project | `irfan-374115` |
| Instance | `instance-20260412-121200` |
| Zone | `us-central1-f` |
| Install root | `/opt/fpl-autopilot` |
| Runtime user | `fpl` |

Do not create a replacement from this runbook unless the owner explicitly requests
disaster recovery or migration. Always run `gcloud projects list` and enumerate
instances across accessible projects before concluding that production is absent.

The historical creation example is retained for disaster recovery only:

```bash
gcloud compute instances create fpl-autopilot-recovery \
  --project=irfan-374115 \
  --zone=us-central1-f \
  --machine-type=e2-small \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=20GB \
  --boot-disk-type=pd-balanced \
  --tags=fpl-autopilot \
  --preemptible=false
```

Notes:
- `e2-small` (2 vCPU/2GB) is enough. Upgrade to `e2-medium` if the MILP solver
  (PuLP/CBC) feels slow near deadline.
- No external static IP needed — SSH via gcloud IAP or `gcloud compute ssh`.
- GW1 deadline is 2026-08-21 17:30 UTC → deploy well before it.

---

## 3. Clone + install

```bash
gcloud compute ssh instance-20260412-121200 --project=irfan-374115 --zone=us-central1-f

# on the VM:
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/lordirfan99/fpl.git /opt/fpl-autopilot
cd /opt/fpl-autopilot
sudo bash deploy/gcp/install.sh
```

Or use a GitHub deploy key (recommended over HTTPS PAT). `install.sh` is
idempotent — safe to re-run.

---

## 4. Verify

```bash
# Bot process up?
systemctl status fpl-telegram.service
journalctl -u fpl-telegram.service -f            # watch startup: "🤖 @Fplnaf_bot polling started"

# Timers armed?
systemctl list-timers 'fpl-*'

# Session works? (from VM)
sudo -u fpl /opt/fpl-autopilot/.venv/bin/python /opt/fpl-autopilot/jobs/token_keepalive.py; echo "rc=$?"

# Tests (should pass):
sudo -u fpl /opt/fpl-autopilot/.venv/bin/python -m unittest discover -s /opt/fpl-autopilot/tests -v | tail -5
```

First run of `token_keepalive.py` will refresh the shipped session. If the
session is IP-bound and bounced, keepalive auto-runs the Camoufox browser login
(needs a headless-capable VM — covered by step 4 of install.sh).

**Confirm in Telegram:** message the bot → /status → it must answer with your
team info (team id 2797967).

---

## 5. Post-deploy security (MANDATORY — do not skip)

The repo ships live secrets by explicit decision. Once the VM is live:

1. **Rotate the Telegram bot token:** BotFather → /mybots → fplnaf_bot → API
   Token → revoke → create new. Put the NEW token in Secret Manager:
   ```bash
   gcloud secrets create fpl_telegram_bot_token --project=irfan-374115
   printf '%s' 'NEW_TOKEN' | gcloud secrets versions add fpl_telegram_bot_token --data-file=- --project=irfan-374115
   ```
2. **Change the FPL password** (my.premierleague.com → account settings).
   Add `fpl_login` / `fpl_password` secrets the same way.
3. Run the bootstrap to pull rotated secrets onto the VM:
   ```bash
   sudo bash /opt/fpl-autopilot/deploy/gcp/secrets-bootstrap.sh
   ```
   The script rewrites `config/credentials.env` (chmod 600) and restarts the bot.
4. **Stop the local Windows system** (already paused: all FPL Hermes crons
   disabled 2026-08-17). Do not run two instances on the same Telegram token —
   the newer poller wins and the older one spams errors.

After rotation, the old tokens in the repo are DEAD — the repo becomes a
safe historical artifact. This is the compromise that makes shipping secrets
in the repo acceptable.

---

## 6. Operations

```bash
# All services: healthy?
systemctl --no-pager list-units 'fpl-*' --all

# Logs
journalctl -u fpl-telegram.service -n 200
tail -f /var/log/fpl/jobs.log /var/log/fpl/bot.log

# Restart bot
sudo systemctl restart fpl-telegram.service

# Stop everything (suspension)
sudo systemctl stop fpl-telegram.service
sudo systemctl disable --now fpl-daily-pull.timer fpl-auto-runner.timer \
  fpl-token-keepalive.timer fpl-auth-canary.timer \
  fpl-squad-watch.timer fpl-approval-reminder.timer fpl-bot-watchdog.timer

# Full re-deploy after pull
cd /opt/fpl-autopilot && sudo git pull && sudo bash deploy/gcp/install.sh
```

### Failure modes

- **Bot dies repeatedly:** check `journalctl -u fpl-telegram.service`; the bot logs
  polling errors and reconnects itself (8s backoff). Heartbeat watchdog
  (`fpl-bot-watchdog.timer`) restarts it if the heartbeat file is >5 min old.
- **Auth failures:** `journalctl -u fpl-token-keepalive.service` — a re-login
  failure is a STOP condition. Run browser_login manually to diagnose:
  `sudo -u fpl /opt/fpl-autopilot/.venv/bin/python /opt/fpl-autopilot/execution/browser_login.py --login`
- **Cloudflare/PingFederate changes:** the auth-canary (1st+15th) catches this
  weeks before a deadline. If the canary fails, fix browser_login.py FIRST.

---

## 7. Optional: Telegram Mini App (not required for core autopilot)

`settings.json` has `telegram.miniapp.public_url` pointing at an ephemeral
trycloudflare tunnel — that dies on reboot and is NOT valid on GCP. Options:

- **Leave it empty** (recommended): bot skips miniapp buttons; the approval
  flow uses the standard inline buttons (still fully functional).
- Or deploy the FastAPI miniapp behind a stable URL (Cloud Run or a fixed LB +
  `post_miniapp_button.py`). Not needed for GW1.

---

## 8. Checklist before GW1 deadline (2026-08-21 17:30 UTC)

- [ ] VM deployed, `install.sh` completed with zero errors
- [ ] `fpl-telegram.service` active and /status answers in Telegram
- [ ] `systemctl list-timers 'fpl-*'` shows all 9 timers
- [ ] `/league` shows `PROVISIONAL` before GW1 deadline and `FINAL` after deadline +5m
- [ ] `token_keepalive.py` exits 0, `/api/me/` verified
- [ ] Manual dry run: `sudo -u fpl … jobs/pre_deadline_run.py` → plan card in Telegram
- [ ] Secrets rotated → `secrets-bootstrap.sh` ran → bot restarted with new token
- [ ] Local Windows crons remain paused (no double instance)
- [ ] Budget check: VM cost ≈ $15–25/month (e2-small, 20GB pd-balanced)
