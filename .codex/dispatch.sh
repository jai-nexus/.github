#!/usr/bin/env bash
set -euo pipefail

ORG="jai-nexus"
REPO=".github"
WF_INV="org_inventory.yml"
WF_HARD="org_hardener.yml"

APP_ID="${ORG_APP_ID:?Set ORG_APP_ID (numeric App ID)}"

# Use either ORG_APP_PRIVATE_KEY_PATH (file path) or ORG_APP_PRIVATE_KEY (PEM text)
if [[ -n "${ORG_APP_PRIVATE_KEY_PATH:-}" ]]; then
  KEYFILE="$ORG_APP_PRIVATE_KEY_PATH"
elif [[ -n "${ORG_APP_PRIVATE_KEY:-}" ]]; then
  KEYFILE="$(mktemp -t ghapp.XXXXXX).pem"
  printf '%s' "$ORG_APP_PRIVATE_KEY" > "$KEYFILE"
else
  echo "Set ORG_APP_PRIVATE_KEY_PATH or ORG_APP_PRIVATE_KEY" >&2
  exit 1
fi

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

iat=$(date -u +%s)
exp=$((iat + 540))   # 9 minutes (must be < 10)
hdr='{"alg":"RS256","typ":"JWT"}'
pld=$(printf '{"iat":%s,"exp":%s,"iss":"%s"}' "$iat" "$exp" "$APP_ID")

hb=$(printf '%s' "$hdr" | b64url)
pb=$(printf '%s' "$pld" | b64url)
sig=$(printf '%s.%s' "$hb" "$pb" | openssl dgst -binary -sha256 -sign "$KEYFILE" | b64url)
JWT="$hb.$pb.$sig"

# Get installation id for the org
inst_id=$(
  curl -fsSL \
    -H "Authorization: Bearer $JWT" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/orgs/$ORG/installation" | \
  awk -F: '/"id":/ {gsub(/[ ,]/,"",$2); print $2; exit}'
)

# Exchange for installation access token
ACCESS_TOKEN=$(
  curl -fsSL -X POST \
    -H "Authorization: Bearer $JWT" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/app/installations/$inst_id/access_tokens" | \
  awk -F'"' '/"token":/ {print $4; exit}'
)

dispatch() {
  local workflow="$1" inputs="$2"
  curl -fsSL -X POST \
    -H "Authorization: token $ACCESS_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$ORG/$REPO/actions/workflows/$workflow/dispatches" \
    -d "{\"ref\":\"main\",\"inputs\":$inputs}"
  echo "✓ Dispatched $workflow with inputs: $inputs"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  inventory)
    subset="${1:-}"; issue="${2:-26}"
    inputs=$(printf '{"subset":"%s","issue_number":"%s"}' "$subset" "$issue")
    dispatch "$WF_INV" "$inputs"
    ;;
  harden)
    dry="${1:-true}"; subset="${2:-}"
    inputs=$(printf '{"dry_run":"%s","subset":"%s"}' "$dry" "$subset")
    dispatch "$WF_HARD" "$inputs"
    ;;
  *)
    echo "Usage:
  ./codex/dispatch.sh inventory <subset-or-empty> <issue-number>
  ./codex/dispatch.sh harden    <true|false>      <subset-or-empty>" >&2
    exit 2
    ;;
esac
