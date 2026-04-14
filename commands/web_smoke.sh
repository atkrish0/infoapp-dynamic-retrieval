#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"

echo "[1/5] GET /health"
health_json="$(curl -sS "${BASE_URL}/health")"
echo "${health_json}"

echo
echo "[2/5] GET /report (status + widget injection check)"
report_status="$(curl -sS -o /tmp/creditcard_report.html -w "%{http_code}" "${BASE_URL}/report")"
echo "status=${report_status}"
if [[ "${report_status}" != "200" ]]; then
  echo "ERROR: /report is not reachable" >&2
  exit 1
fi
if ! grep -q "/static/creditcard_widget.js" /tmp/creditcard_report.html; then
  echo "ERROR: widget script not injected into /report output" >&2
  exit 1
fi
echo "widget_injected=true"

echo
echo "[3/5] POST /agent/dispatch (row lookup)"
row_payload='{"query":"show row for Cube Eatery on 2022-01-01","doc_id":"creditcard.xlsx","dataset_id":"Sheet_1","context":{"title":"creditcard","url":"'"${BASE_URL}"'/report"}}'
row_json="$(curl -sS -X POST "${BASE_URL}/agent/dispatch" -H "Content-Type: application/json" -d "${row_payload}")"
echo "${row_json}"

echo
echo "[4/5] POST /agent/dispatch (aggregate)"
agg_payload='{"query":"total charge for March 2022","doc_id":"creditcard.xlsx","dataset_id":"Sheet_1","context":{"title":"creditcard","url":"'"${BASE_URL}"'/report"}}'
agg_json="$(curl -sS -X POST "${BASE_URL}/agent/dispatch" -H "Content-Type: application/json" -d "${agg_payload}")"
echo "${agg_json}"

echo
echo "[5/5] POST /agent/dispatch (count)"
count_payload='{"query":"count transactions for Lunch tag","doc_id":"creditcard.xlsx","dataset_id":"Sheet_1","context":{"title":"creditcard","url":"'"${BASE_URL}"'/report"}}'
count_json="$(curl -sS -X POST "${BASE_URL}/agent/dispatch" -H "Content-Type: application/json" -d "${count_payload}")"
echo "${count_json}"

echo
echo "Smoke checks finished."
