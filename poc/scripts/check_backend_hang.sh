#!/usr/bin/env bash
# Focused diagnostic for the "backend hangs after first few requests" symptom.
# Run from anywhere on rlcc-app01:
#     bash ~/rlcc/poc/scripts/check_backend_hang.sh
#
# Read-only. Does not modify anything. Tells us whether the backend is
# truly idle/blocked, how big the data files are, and what the latest log
# entries look like.

set +e

LOG_DIR="$HOME/rlcc/poc/logs"
DATA_DIR="$HOME/rlcc/poc/data"

section() {
  printf "\n========== %s ==========\n" "$1"
}

section "1. RECENT BACKEND LOG (last 40 lines)"
tail -40 "$LOG_DIR/backend.log" 2>&1

section "2. DATA FILE SIZES"
ls -lh "$DATA_DIR"/*.jsonl 2>/dev/null
echo "--- line counts ---"
wc -l "$DATA_DIR"/*.jsonl 2>/dev/null

section "3. EVENTS DIR"
echo "files: $(find "$DATA_DIR/events" -maxdepth 1 -type f 2>/dev/null | wc -l)"
du -sh "$DATA_DIR/events" 2>/dev/null

section "4. SUSTAINED /health TIMING (5 calls, 5s apart)"
for i in 1 2 3 4 5; do
  curl -m 10 -s -o /dev/null -w "[$i] HTTP %{http_code}  time %{time_total}s\n" http://localhost:8001/health
  [ $i -lt 5 ] && sleep 5
done

section "5. OTHER ENDPOINTS"
for ep in /api/cameras /api/stores /api/alerts /api/transactions; do
  printf "%-22s " "$ep"
  curl -m 10 -s -o /dev/null -w "HTTP %{http_code}  time %{time_total}s\n" "http://localhost:8001$ep"
done

section "6. BACKEND WORKER STATE"
backend_pid=$(pgrep -f "backend.main" | tail -1)
echo "worker pid: ${backend_pid:-?}"
if [ -n "$backend_pid" ]; then
  echo "-- top --"
  top -bn1 -p "$backend_pid" 2>&1 | tail -3
  echo "-- file descriptors --"
  echo "fd count: $(ls /proc/$backend_pid/fd 2>/dev/null | wc -l)"
  echo "-- threads --"
  ls /proc/$backend_pid/task 2>/dev/null | wc -l
  echo "-- recent syscalls (5s sample) --"
  timeout 5 strace -p "$backend_pid" -c 2>&1 | tail -20
fi

section "7. FFMPEG PROCESSES (should be 8)"
echo "count: $(pgrep -af ffmpeg | wc -l)"
pgrep -af ffmpeg | head -12

section "8. DISK + LOAD"
df -h "$DATA_DIR" 2>&1 | head -3
uptime

printf "\n========== END ==========\n"
