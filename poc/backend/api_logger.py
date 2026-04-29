"""Structured JSONL logger for inbound/outbound RLCC API traffic.

Writes one record per request leg (`in` and `out`) to:
  - poc/logs/api.jsonl  (rotated at 20 MB, 10 backups)
  - stdout              (so `journalctl -u rlcc` / `start.sh` tail picks it up)

Two records share the same `request_id` so post-processing can pair them.
The `x-authorization-key` header is redacted; bodies are capped at 4 KB.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_LOG_FILE = _LOGS_DIR / "api.jsonl"


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("rlcc.api")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False  # don't double-emit through uvicorn's root logger

    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("[rlcc.api] %(message)s"))
    logger.addHandler(sh)
    return logger


_logger = _build_logger()


def log_event(direction: str, **fields: Any) -> None:
    """Emit a single JSONL record. `direction` is "in" or "out"."""
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        **fields,
    }
    try:
        line = json.dumps(record, default=str, ensure_ascii=False)
    except Exception:
        line = json.dumps({"ts": record["ts"], "direction": direction, "error": "json-serialize-failed"})
    _logger.info(line)
