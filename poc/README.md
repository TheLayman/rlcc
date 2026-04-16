# RLCC POC

Fraud detection system for 5 airport retail stores. Runs on a single T4 workstation.

## Setup

```bash
cd poc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
# Start Redis
redis-server --daemonize yes --dir ./data

# Start backend
python -m backend.main

# In another terminal, run emulators
python -m emulator.nukkad_emulator
python -m emulator.cv_emulator
```

## Test

```bash
cd poc
pytest -v
```
