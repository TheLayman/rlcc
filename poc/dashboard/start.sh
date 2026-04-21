#!/bin/bash
# RLCC POC — Start all services
# Usage: ./start.sh
#        ./start.sh stop       (kill all services)

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

stop_all() {
    echo "Killing existing services..."
    fuser -k 8000/tcp 2>/dev/null
    fuser -k 8001/tcp 2>/dev/null
    fuser -k 5173/tcp 2>/dev/null
    sleep 1
    echo "All ports cleared."
}

if [ "$1" = "stop" ]; then
    stop_all
    exit 0
fi

stop_all

echo "Starting Backend (port 8001)..."
cd "$ROOT_DIR/Retail-Trust-Backend-Service"
nohup python3 main.py > backend.log 2>&1 &
echo "  PID: $!"

echo "Starting CV Pipeline (port 8000)..."
cd "$ROOT_DIR/fds-cv"
nohup python3 fds_hudson/vas_server.py --zones zones.json --port 8000 > cv.log 2>&1 &
echo "  PID: $!"

echo "Starting Dashboard (port 5173)..."
cd "$ROOT_DIR/Retail-trust-and-Security-Dashboard"
nohup npm run dev -- --host 0.0.0.0 > dashboard.log 2>&1 &
echo "  PID: $!"

sleep 2
echo ""
echo "All services started."
echo "  Dashboard:  http://<this-machine-ip>:5173"
echo "  Backend:    http://localhost:8001"
echo "  CV Stream:  http://localhost:8000/stream"
echo ""
echo "Logs:"
echo "  tail -f $ROOT_DIR/Retail-Trust-Backend-Service/backend.log"
echo "  tail -f $ROOT_DIR/fds-cv/cv.log"
echo "  tail -f $ROOT_DIR/Retail-trust-and-Security-Dashboard/dashboard.log"
echo ""
echo "To stop: ./start.sh stop"
