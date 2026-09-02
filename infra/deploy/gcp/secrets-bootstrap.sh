#!/bin/bash
# FPL Autopilot - pull secrets from GCP Secret Manager into config/.
# Use AFTER rotating tokens (post-deploy hygiene). Requires gcloud auth.
#
#   gcloud auth application-default login        # or: gcloud auth login
#   bash deploy/gcp/secrets-bootstrap.sh
#
# Creates/updates secrets if missing, then writes config/credentials.env
# (chmod 600) and config/fpl_session.json (empty until first browser login).
#
# Secret names (Google Secret Manager): fpl_login, fpl_password,
# fpl_telegram_bot_token, fpl_session_json.
set -euo pipefail

APP_DIR=/opt/fpl-autopilot
PROJECT=${GCP_PROJECT_ID:-"swift-rite-497623-c3"}
REGION=${GCP_REGION:-"asia-southeast1-a"}
CREDS="$APP_DIR/config/credentials.env"
SESSION="$APP_DIR/config/fpl_session.json"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI missing. Install via: snap install google-cloud-cli --classic" >&2
  exit 1
fi

# Writing helpers use random suffixes to survive partially-rolled secrets.
rand() { head -c 8 /dev/urandom | xxd -p; }
write_secret() {
  # write_secret <name> <value>  (creates or updates the newest version)
  local name="$1" value="$2" f
  f="$TMP/$name.$(rand)"
  printf '%s' "$value" > "$f"
  if gcloud secrets describe "$name" --project="$PROJECT" >/dev/null 2>&1; then
    gcloud secrets versions add "$name" --data-file="$f" --project="$PROJECT" >/dev/null
  else
    gcloud secrets create "$name" --data-file="$f" --project="$PROJECT" >/dev/null
  fi
}

echo "==> Writing secrets to Secret Manager ($PROJECT)"
declare -A VALS
while IFS= read -r line; do
  line="${line//$'\r'/}"
  [[ -z "$line" || "$line" == \#* ]] && continue
  key="${line%%=*}"; val="${line#*=}"
  VALS["$key"]="$val"
done < "$CREDS"

write_secret "fpl_login" "${VALS[FPL_LOGIN]:-}"
write_secret "fpl_password" "${VALS[FPL_PASSWORD]:-}"
write_secret "fpl_telegram_bot_token" "${VALS[TELEGRAM_BOT_TOKEN]:-}"
if [ -s "$SESSION" ]; then
  write_secret "fpl_session_json" "$(cat "$SESSION")"
fi

echo "==> Rebuilding config/credentials.env from Secret Manager"
cat > "$CREDS" <<'EOF'
EOF
for name in fpl_login fpl_password fpl_telegram_bot_token; do
  val=$(gcloud secrets versions access latest --secret="$name" --project="$PROJECT" 2>/dev/null || echo "")
  case "$name" in
    fpl_login)               echo "FPL_LOGIN=${val}" >> "$CREDS" ;;
    fpl_password)            echo "FPL_PASSWORD=${val}" >> "$CREDS" ;;
    fpl_telegram_bot_token)  echo "TELEGRAM_BOT_TOKEN=${val}" >> "$CREDS" ;;
  esac
done
chmod 600 "$CREDS"
chmod 600 "$SESSION"

echo "==> Restarting bot to load fresh secrets"
systemctl restart fpl-bot.service || true

echo "DONE. Secrets are now in Secret Manager and config/. Remember to DELETE"
echo "the old tokens from the repo history once rotation is complete."