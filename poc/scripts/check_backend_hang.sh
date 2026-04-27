#!/usr/bin/env bash
# Focused diagnostic for the "backend hangs / dashboard disconnected" symptom.
# Run from anywhere on rlcc-app01:
#     bash ~/rlcc/poc/scripts/check_backend_hang.sh
#
# Read-only. Does not modify anything.

set +e

LOG_DIR="$HOME/rlcc/poc/logs"
DATA_DIR="$HOME/rlcc/poc/data"

section() {
  printf "\n========== %s ==========\n" "$1"
}

section "1. RECENT BACKEND LOG (last 50 lines)"
tail -50 "$LOG_DIR/backend.log" 2>&1

section "2. ALL backend.main / cv.main PROCESSES"
ps -ef | grep -E "backend.main|cv.main" | grep -v grep

section "3. WHAT IS LISTENING ON 8001"
ss -tlnp 2>/dev/null | grep 8001 || echo "(nothing listening on 8001)"

section "4. VERBOSE CURL TO BACKEND /health"
curl --connect-timeout 3 -m 20 -v http://127.0.0.1:8001/health 2>&1 | head -30

section "5. IPv4 EXPLICIT TIMINGS (5 calls, 5s apart)"
for i in 1 2 3 4 5; do
  curl -m 15 -s -o /dev/null -w "[$i] HTTP %{http_code}  time %{time_total}s\n" http://127.0.0.1:8001/health
  [ $i -lt 5 ] && sleep 5
done

section "6. IS LOOPBACK BLOCKED BY iptables?"
sudo -n iptables -L INPUT -n 2>/dev/null | head -10 || echo "(no sudo or no iptables — skipping)"

section "7. CV /health REACHES via 127.0.0.1?"
curl -m 5 -sf http://127.0.0.1:8000/health > /dev/null && echo "✓ CV /health OK on 127.0.0.1" || echo "✗ CV also down on 127.0.0.1"

section "8. DATA FILE SIZES"
ls -lh "$DATA_DIR"/*.jsonl 2>/dev/null
echo "--- line counts ---"
wc -l "$DATA_DIR"/*.jsonl 2>/dev/null

section "9. BACKEND WORKER STATE"
backend_pids=$(pgrep -f "backend.main")
echo "all backend.main pids: $backend_pids"
for pid in $backend_pids; do
  echo "-- pid $pid --"
  ps -p "$pid" -o pid,ppid,pcpu,pmem,etime,stat,cmd 2>&1 | tail -2
  echo "fd count: $(ls /proc/$pid/fd 2>/dev/null | wc -l)"
done

section "10. FFMPEG PROCESSES (should be 8)"
echo "count: $(pgrep -af ffmpeg | wc -l)"

section "11. DISK + LOAD"
df -h "$DATA_DIR" 2>&1 | head -3
uptime

printf "\n========== END ==========\n"
