#!/usr/bin/env python3
"""Pre-flight check: given a list of (store_id, pos_terminal_no) pairs that
the POS push API will send, report which are mapped, which need a camera,
and which have an unknown store.

Usage (from the ``poc/`` directory):

    # Inline pairs
    python3 scripts/validate_pos_mappings.py NDCIN1231:POS1 NDCIN1422:POS5

    # From a file (one pair per line, "store_id,pos" or "store_id POS X")
    python3 scripts/validate_pos_mappings.py --file mappings.txt

    # Read from stdin (one pair per line)
    echo "NDCIN1227 POS 2" | python3 scripts/validate_pos_mappings.py --stdin

Exit code is 0 when every pair resolves to an enabled camera, 1 otherwise.
The script does NOT modify config — purely advisory.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


POC_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC_DIR))

from backend.config import Config  # noqa: E402  (path mutation needed first)


CONFIG_DIR = POC_DIR / "config"


def _parse_pair(raw: str) -> tuple[str, str] | None:
    text = raw.strip()
    if not text or text.startswith("#"):
        return None
    # Accept "store:pos", "store,pos", "store pos" (with internal space in pos)
    for sep in (":", ","):
        if sep in text:
            store, pos = text.split(sep, 1)
            return store.strip(), pos.strip()
    parts = re.split(r"\s+", text, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None


def _gather_pairs(args: argparse.Namespace) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw in args.pairs or []:
        parsed = _parse_pair(raw)
        if parsed:
            pairs.append(parsed)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            for line in fh:
                parsed = _parse_pair(line)
                if parsed:
                    pairs.append(parsed)
    if args.stdin:
        for line in sys.stdin:
            parsed = _parse_pair(line)
            if parsed:
                pairs.append(parsed)
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pairs",
        nargs="*",
        help="store:pos pairs to validate (e.g. NDCIN1231:POS1)",
    )
    parser.add_argument("--file", help="path to file with one pair per line")
    parser.add_argument("--stdin", action="store_true", help="read pairs from stdin")
    args = parser.parse_args()

    pairs = _gather_pairs(args)
    if not pairs:
        parser.error("no pairs supplied — pass them inline, via --file, or --stdin")

    config = Config(config_dir=str(CONFIG_DIR))
    known_stores = {s.cin for s in config.stores}

    width_store = max(8, max(len(s) for s, _ in pairs))
    width_pos = max(6, max(len(p) for _, p in pairs))
    fmt = f"  {{:<{width_store}}}  {{:<{width_pos}}}  {{}}"

    print(fmt.format("STORE", "POS", "STATUS"))
    print(fmt.format("-" * width_store, "-" * width_pos, "-" * 60))

    failures = 0
    fallback_used = 0
    for store, pos in pairs:
        store_known = store in known_stores
        camera = config.get_camera_by_terminal(store, pos)
        if camera is None:
            failures += 1
            if not store_known:
                msg = "MISSING — store not in stores.json AND no camera mapped"
            else:
                msg = "MISSING — store known but no camera for this POS terminal"
            print(fmt.format(store, pos, msg))
            continue

        store_label = config.get_store_name(store) or "(no name)"
        normalized_input = "".join(pos.upper().split())
        if camera.normalized_terminal != normalized_input:
            fallback_used += 1
            print(
                fmt.format(
                    store,
                    pos,
                    f"OK via fallback — {camera.camera_id} ({store_label}); "
                    f"match_any_pos_in_store=True",
                )
            )
        else:
            print(fmt.format(store, pos, f"OK — {camera.camera_id} ({store_label})"))

    print()
    print(
        f"summary: {len(pairs)} pair(s) checked, {failures} unmapped, "
        f"{fallback_used} via store-wide fallback"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
