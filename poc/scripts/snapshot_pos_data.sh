#!/usr/bin/env bash
# snapshot_pos_data.sh — captures everything we need to see how live POS
# data is landing. Run from the poc/ root:
#
#     bash scripts/snapshot_pos_data.sh
#
# Or pipe to a file:
#
#     bash scripts/snapshot_pos_data.sh > /tmp/pos_snapshot.txt 2>&1
#
# Then paste the output back. Safe to re-run — read-only.

set -u
BACKEND="${BACKEND_URL:-http://localhost:8001}"
DATA_DIR="${DATA_DIR:-data}"
LOG_FILE="${LOG_FILE:-logs/backend.log}"
TODAY="$(date +%F)"
EVENTS_FILE="${DATA_DIR}/events/${TODAY}.jsonl"
TXN_FILE="${DATA_DIR}/transactions.jsonl"
ALERT_FILE="${DATA_DIR}/alerts.jsonl"

hr() { printf '\n========== %s ==========\n' "$*"; }
note() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

if have jq; then
    JQ="jq"
else
    JQ="cat"  # graceful fallback — raw JSON if jq missing
    note "WARNING: jq not installed — output will be raw JSON. (apt install jq)"
fi

hr "ENVIRONMENT"
note "host:        $(hostname)"
note "date (UTC):  $(date -u +'%F %T %Z')"
note "date (IST):  $(TZ=Asia/Kolkata date +'%F %T %Z')"
note "pwd:         $(pwd)"
note "backend url: ${BACKEND}"
note "data dir:    ${DATA_DIR}"
note "log file:    ${LOG_FILE}"

hr "BACKEND HEALTH (/health)"
if have curl; then
    curl -sS --max-time 5 "${BACKEND}/health" | $JQ . 2>/dev/null || note "(/health unreachable or returned non-JSON)"
else
    note "curl missing — skip"
fi

hr "BACKEND CONTRACT (/v1/rlcc/contract) — what we advertise"
curl -sS --max-time 5 "${BACKEND}/v1/rlcc/contract" 2>/dev/null \
    | $JQ '{events: [.endpoints[].event], paths: [.endpoints[].path]}' 2>/dev/null \
    || note "(contract unreachable)"

hr "RAW POS EVENTS — counts per endpoint (today: ${TODAY})"
if [[ -f "${EVENTS_FILE}" ]]; then
    note "file: ${EVENTS_FILE}"
    note "total lines: $(wc -l < "${EVENTS_FILE}")"
    note ""
    note "events received per type today:"
    if have jq; then
        jq -r '.event // "UNKNOWN"' "${EVENTS_FILE}" | sort | uniq -c | sort -rn
    else
        grep -oE '"event":"[^"]*"' "${EVENTS_FILE}" | sort | uniq -c | sort -rn
    fi
    note ""
    note "stores seen today:"
    if have jq; then
        jq -r '.storeIdentifier // "MISSING"' "${EVENTS_FILE}" | sort | uniq -c | sort -rn | head -20
    fi
    note ""
    note "POS terminals seen today:"
    if have jq; then
        jq -r '"\(.storeIdentifier // "?") | \(.posTerminalNo // "?")"' "${EVENTS_FILE}" \
            | sort | uniq -c | sort -rn | head -30
    fi
else
    note "(no events file at ${EVENTS_FILE} — nothing pushed today, or DATA_DIR wrong)"
    note ""
    note "available event files:"
    ls -lah "${DATA_DIR}/events/" 2>/dev/null | tail -10
fi

hr "RAW POS EVENTS — last 5 full payloads (most recent first)"
if [[ -f "${EVENTS_FILE}" ]] && have jq; then
    tail -5 "${EVENTS_FILE}" | tac | jq .
elif [[ -f "${EVENTS_FILE}" ]]; then
    tail -5 "${EVENTS_FILE}" | tac
else
    note "(no events file)"
fi

hr "RAW POS EVENTS — one sample of EACH event type seen today"
if [[ -f "${EVENTS_FILE}" ]] && have jq; then
    for ev in $(jq -r '.event' "${EVENTS_FILE}" | sort -u); do
        note ""
        note "--- ${ev} ---"
        jq -c "select(.event == \"${ev}\")" "${EVENTS_FILE}" | head -1 | jq .
    done
fi

hr "ASSEMBLED TRANSACTIONS — last 10 (data/transactions.jsonl)"
if [[ -f "${TXN_FILE}" ]]; then
    note "total transactions: $(wc -l < "${TXN_FILE}")"
    if have jq; then
        tail -10 "${TXN_FILE}" | jq -c '{
            id, store_id, pos_terminal_no, status, source,
            started_at, committed_at, bill_number,
            n_items: (.items|length), n_payments: (.payments|length),
            risk_level, triggered_rules,
            cv_confidence, cv_non_seller_present, cv_receipt_detected,
            cv_bill_hand_present, cv_screen_motion,
            camera_id, snippet: (if (.snippet_path|length) > 0 then "yes" else "" end)
        }'
    else
        tail -10 "${TXN_FILE}"
    fi
else
    note "(no ${TXN_FILE})"
fi

hr "ALERTS — last 10 (data/alerts.jsonl)"
if [[ -f "${ALERT_FILE}" ]]; then
    note "total alerts: $(wc -l < "${ALERT_FILE}")"
    if have jq; then
        tail -10 "${ALERT_FILE}" | jq -c '{
            id, transaction_id, store_id, pos_terminal_no,
            risk_level, triggered_rules, source, status,
            timestamp, cv_confidence,
            camera_id, snippet: (if (.snippet_path|length) > 0 then "yes" else "" end)
        }'
    else
        tail -10 "${ALERT_FILE}"
    fi
else
    note "(no ${ALERT_FILE})"
fi

hr "BACKEND LOG — last 80 lines of ${LOG_FILE}"
if [[ -f "${LOG_FILE}" ]]; then
    tail -80 "${LOG_FILE}"
else
    note "(no ${LOG_FILE})"
fi

hr "BACKEND LOG — receiver/correlator/auth lines, last 40"
if [[ -f "${LOG_FILE}" ]]; then
    grep -Ei 'receiver|correlat|UNMAPPED|UNAVAILABLE|push_assembled|Unauthorized|x-authorization|rate.?limit|begin-transaction|commit-transaction' \
        "${LOG_FILE}" | tail -40
fi

hr "BACKEND LOG — errors/tracebacks, last 40"
if [[ -f "${LOG_FILE}" ]]; then
    grep -Ei 'error|traceback|exception|critical|warn' "${LOG_FILE}" | tail -40
fi

hr "CV WINDOWS — what correlator can see now"
if have curl; then
    curl -sS --max-time 5 "${BACKEND}/health" 2>/dev/null \
        | $JQ '.cv // "no cv field in /health"' 2>/dev/null
fi

hr "CONFIG SNAPSHOT — store/POS pairs we know"
if [[ -f config/camera_mapping.json ]] && have jq; then
    jq -r '.[] | "\(.store_id) | \(.pos_terminal_no) | \(.camera_id) | enabled=\(.enabled)"' \
        config/camera_mapping.json
fi

hr "DISK + CLIPS"
df -h "${DATA_DIR}" 2>/dev/null | head -2
note ""
note "snippets dir:"
ls -lah "${DATA_DIR}/snippets/" 2>/dev/null | tail -15 || note "(no snippets dir)"

hr "DONE — paste everything above this line back to the chat"
