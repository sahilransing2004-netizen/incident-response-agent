#!/usr/bin/env bash
# Sends a test HighCPU alert to the agent and prints the analysis.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_URL="${BASE_URL:-http://localhost:8001}"
JOB="${JOB:-demo}"
TOKEN="$(grep '^WEBHOOK_TOKEN=' .env | cut -d= -f2- | tr -d '\r"')"
FP="test-$(date +%s)"

PAYLOAD=$(cat <<JSON
{"alerts": [{"status": "firing", "fingerprint": "$FP",
  "labels": {"alertname": "HighCPU", "severity": "critical", "job": "$JOB"},
  "annotations": {"summary": "CPU usage at 90% on $JOB"}}]}
JSON
)

RESP=$(curl -sf -X POST "$BASE_URL/webhook/alertmanager" \
  -H "X-Webhook-Token: $TOKEN" -H "Content-Type: application/json" -d "$PAYLOAD")
echo "webhook response: $RESP"

ID=$(printf '%s' "$RESP" | python3 -c "import sys,json; a=json.load(sys.stdin)['accepted']; print(a[0] if a else '')")
[ -n "$ID" ] || { echo "alert not accepted (duplicate fingerprint?)"; exit 1; }

echo "waiting for analysis of incident $ID (up to 150s)..."
OUT=""
for _ in $(seq 1 30); do
  OUT=$(curl -sf "$BASE_URL/incidents/$ID" -H "X-Webhook-Token: $TOKEN")
  if printf '%s' "$OUT" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin).get('root_cause') else 1)"; then break; fi
  sleep 5
done
printf '%s' "$OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ROOT CAUSE:', d.get('root_cause')); print('REMEDIATION:', d.get('remediation'))"
