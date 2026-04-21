#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
POC_DIR="$ROOT_DIR/poc"
VENV_DIR="$POC_DIR/.venv"
ENV_FILE="$POC_DIR/.env"
LOG_DIR="$POC_DIR/logs"
DATA_DIR="$POC_DIR/data"
REDIS_DIR="$DATA_DIR/redis"
REDIS_PID="$REDIS_DIR/redis.pid"
REDIS_CONF="$REDIS_DIR/redis.conf"

fail() {
  echo "start.sh: $1" >&2
  exit 1
}

require_file() {
  [ -f "$1" ] || fail "Missing required file: $1"
}

ensure_dirs() {
  mkdir -p "$LOG_DIR" "$DATA_DIR/redis" "$DATA_DIR/buffer" "$DATA_DIR/snippets" "$DATA_DIR/events"
}

stop_port() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" 2>/dev/null || true
  fi
}

write_redis_conf() {
  cat > "$REDIS_CONF" <<EOF
bind 127.0.0.1
port 6379
dir $REDIS_DIR
pidfile $REDIS_PID
logfile $LOG_DIR/redis.log
daemonize yes
save ""
appendonly no
protected-mode yes
EOF
}

start_redis() {
  if command -v redis-cli >/dev/null 2>&1 && redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; then
    echo "Redis already running on 127.0.0.1:6379"
    return
  fi
  write_redis_conf
  redis-server "$REDIS_CONF"
}

stop_redis() {
  if [ -f "$REDIS_PID" ]; then
    local redis_pid
    redis_pid="$(cat "$REDIS_PID")"
    if [ -n "$redis_pid" ] && kill -0 "$redis_pid" >/dev/null 2>&1; then
      kill "$redis_pid" || true
    fi
    rm -f "$REDIS_PID"
  fi
}

stop_all() {
  echo "Stopping RLCC services..."
  stop_port 8000
  stop_port 8001
  stop_port 5173
  stop_redis
  echo "Ports cleared: 8000, 8001, 5173"
}

if [ "${1:-}" = "stop" ]; then
  stop_all
  exit 0
fi

require_file "$ENV_FILE"
require_file "$POC_DIR/config/stores.json"
require_file "$POC_DIR/config/camera_mapping.json"
require_file "$POC_DIR/config/rule_config.json"
[ -d "$VENV_DIR" ] || fail "Missing virtualenv: $VENV_DIR. Run ./bootstrap.sh first."
[ -d "$POC_DIR/dashboard" ] || fail "Missing dashboard app: $POC_DIR/dashboard"
[ -d "$POC_DIR/dashboard/node_modules" ] || fail "Dashboard dependencies are missing. Run ./bootstrap.sh first."

ensure_dirs
stop_all
start_redis

echo "Starting RLCC CV service on :8000 ..."
(
  cd "$POC_DIR"
  . "$VENV_DIR/bin/activate"
  nohup python -m cv.main > "$LOG_DIR/cv.log" 2>&1 &
  echo $! > "$DATA_DIR/cv.pid"
)

echo "Starting RLCC backend on :8001 ..."
(
  cd "$POC_DIR"
  . "$VENV_DIR/bin/activate"
  nohup python -m backend.main > "$LOG_DIR/backend.log" 2>&1 &
  echo $! > "$DATA_DIR/backend.pid"
)

echo "Starting RLCC dashboard on :5173 ..."
(
  cd "$POC_DIR/dashboard"
  nohup npm run dev -- --host 0.0.0.0 --port 5173 > "$LOG_DIR/dashboard.log" 2>&1 &
  echo $! > "$DATA_DIR/dashboard.pid"
)

sleep 2

cat <<EOF

RLCC services started.

Dashboard:    http://<server-ip>:5173
Backend:      http://<server-ip>:8001
Push API:     POST http://<server-ip>:8001/v1/rlcc/launch-event
CV Debug:     http://<server-ip>:8000/stream/view
Redis:        127.0.0.1:6379

Logs:
  $LOG_DIR/backend.log
  $LOG_DIR/cv.log
  $LOG_DIR/dashboard.log
  $LOG_DIR/redis.log

Stop everything:
  ./start.sh stop
EOF
