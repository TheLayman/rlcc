#!/usr/bin/env python3
"""Backfill historical alerts and transactions with the current camera mapping.

Alerts and transactions persisted before their POS terminal was mapped to a
camera have ``camera_id`` / ``display_pos_label`` / ``device_id`` left blank,
which makes them show up as "Camera unmapped" in the dashboard even after the
mapping has been added.  This one-shot script walks the JSONL files and fills
those fields in from ``config/camera_mapping.json``.

Usage (run from the ``poc/`` directory):

    python3 scripts/backfill_camera_mapping.py           # dry run
    python3 scripts/backfill_camera_mapping.py --apply   # write changes

``--apply`` creates ``data/alerts.jsonl.bak`` / ``data/transactions.jsonl.bak``
first, then rewrites the originals atomically.  Re-run the dashboard refresh
afterwards to see the reasons clear.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


POC_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC_DIR))

from backend.config import Config  # noqa: E402  (path mutation required first)


DATA_DIR = POC_DIR / "data"
CONFIG_DIR = POC_DIR / "config"


def _hydrate_record(record: dict, config: Config) -> bool:
    """Return True if we filled in any camera field."""
    if record.get("camera_id"):
        return False
    store_id = record.get("store_id", "")
    pos = record.get("pos_terminal_no") or record.get("display_pos_label") or ""
    if not store_id or not pos:
        return False
    camera = config.get_camera_by_terminal(store_id, pos)
    if not camera:
        return False
    record["camera_id"] = camera.camera_id
    if not record.get("display_pos_label"):
        record["display_pos_label"] = camera.display_pos_label
    if not record.get("pos_terminal_no"):
        record["pos_terminal_no"] = camera.pos_terminal_no
    if not record.get("device_id"):
        record["device_id"] = camera.xprotect_device_id
    if not record.get("seller_window_id"):
        record["seller_window_id"] = camera.seller_window_id
    return True


def _process(path: Path, config: Config, apply: bool) -> tuple[int, int]:
    if not path.exists():
        print(f"  skipped (missing): {path}")
        return 0, 0

    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    changed = 0
    total = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        total += 1
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            output.append(line)
            continue
        if _hydrate_record(record, config):
            changed += 1
        output.append(json.dumps(record))

    print(f"  {path.name}: {changed}/{total} records backfilled")

    if apply and changed:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy(path, backup)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(output) + "\n", encoding="utf-8")
        tmp.replace(path)
        print(f"    wrote {path}; backup at {backup}")

    return changed, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually rewrite the JSONL files (default is dry-run)",
    )
    args = parser.parse_args()

    print(f"[backfill] poc dir: {POC_DIR}")
    config = Config(config_dir=str(CONFIG_DIR))
    print(f"[backfill] cameras loaded: {len(config.cameras)}")
    print(f"[backfill] mode: {'apply' if args.apply else 'dry-run (no changes written)'}")

    total_changed = 0
    for name in ("alerts.jsonl", "transactions.jsonl"):
        changed, _ = _process(DATA_DIR / name, config, args.apply)
        total_changed += changed

    if not args.apply and total_changed:
        print("\nRe-run with --apply to persist the changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
