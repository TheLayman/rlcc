#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
POC_DIR="$ROOT_DIR/poc"
VENV_DIR="$POC_DIR/.venv"
TORCH_INDEX_URL="${TORCH_WHL_INDEX_URL:-https://download.pytorch.org/whl/cu124}"

UBUNTU_PACKAGES=(
  python3
  python3-venv
  python3-pip
  python3-dev
  build-essential
  ffmpeg
  redis-server
  curl
  git
  ca-certificates
  pkg-config
  psmisc
  libgl1
  libglib2.0-0
  libgomp1
  libsm6
  libxext6
  libxrender1
)

missing_packages=()
for package in "${UBUNTU_PACKAGES[@]}"; do
  if ! dpkg -s "$package" >/dev/null 2>&1; then
    missing_packages+=("$package")
  fi
done

if [ "${#missing_packages[@]}" -gt 0 ]; then
  sudo apt-get update
  sudo apt-get install -y "${missing_packages[@]}"
fi

if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
else
  NODE_MAJOR=0
fi

if [ "$NODE_MAJOR" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

mkdir -p "$POC_DIR/logs" "$POC_DIR/data/redis" "$POC_DIR/data/buffer" "$POC_DIR/data/snippets" "$POC_DIR/data/events"

python3 -m venv "$VENV_DIR"
. "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install --index-url "$TORCH_INDEX_URL" torch torchvision
python -m pip install -r "$POC_DIR/requirements-backend.txt"
python -m pip install -r "$POC_DIR/requirements-cv.txt"
python -m pip install -r "$POC_DIR/requirements-dev.txt"

cd "$POC_DIR/dashboard"
if [ -f package-lock.json ]; then
  npm ci || npm install
else
  npm install
fi

python - <<'PY'
import fastapi
import httpx
import redis
import cv2
import ultralytics
import torch

print("fastapi ok")
print("httpx ok")
print("redis ok")
print("cv2 ok")
print("ultralytics ok")
print(f"cuda={torch.cuda.is_available()}")
if not torch.cuda.is_available():
    raise SystemExit("CUDA is not available. Install/verify the NVIDIA driver and CUDA runtime before starting RLCC.")
PY

redis-server --version
ffmpeg -version | head -n 1
node -v
npm -v

echo
echo "Bootstrap complete."
echo "Next:"
echo "  1. cp \"$POC_DIR/.env.example\" \"$POC_DIR/.env\""
echo "  2. Edit \"$POC_DIR/.env\" and \"$POC_DIR/config/camera_mapping.json\""
echo "  3. Run \"$ROOT_DIR/start.sh\""
