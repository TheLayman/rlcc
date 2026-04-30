#!/usr/bin/env python3
"""
Wipe POC test state so you can start fresh against live data.

Removes everything the running backend has accumulated: API traffic logs,
raw event stream, assembled transactions, fired alerts, video buffer
segments, and saved snippets. Leaves config (stores.json, camera_mapping.json,
rule_config.json), source code, and `.env` strictly alone.

Run between test sessions, or once before the live POC kicks off.

Usage:
    python3 poc/scripts/reset_poc_state.py             # interactive: prints targets, asks Y/n
    python3 poc/scripts/reset_poc_state.py --yes       # non-interactive
    python3 poc/scripts/reset_poc_state.py --dry-run   # show what would be deleted, don't delete
    python3 poc/scripts/reset_poc_state.py --keep-buffer  # spare the rolling video buffer

Stop the backend + CV services before running for predictable results
(otherwise ffmpeg will recreate buffer segments while we're deleting them).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


POC_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = POC_DIR / "data"
LOGS_DIR = POC_DIR / "logs"


def _safe_glob(parent: Path, pattern: str) -> list[Path]:
    """List files under parent matching pattern. Returns [] if parent missing."""
    if not parent.is_dir():
        return []
    return sorted(parent.glob(pattern))


def collect_targets(keep_buffer: bool) -> list[tuple[str, list[Path]]]:
    groups: list[tuple[str, list[Path]]] = []

    # 1. API traffic log + rotations
    groups.append(
        ("API traffic logs (poc/logs/api.jsonl*)", _safe_glob(LOGS_DIR, "api.jsonl*"))
    )

    # 2. Other backend / CV / uvicorn logs
    groups.append(
        ("Other logs (poc/logs/*.log)", _safe_glob(LOGS_DIR, "*.log"))
    )

    # 3. Raw push event stream
    groups.append(
        ("Raw push event stream (poc/data/events/*.jsonl)",
         _safe_glob(DATA_DIR / "events", "*.jsonl"))
    )

    # 4. Assembled transactions
    txns = DATA_DIR / "transactions.jsonl"
    groups.append(("Assembled transactions (poc/data/transactions.jsonl)",
                   [txns] if txns.exists() else []))

    # 5. Fired alerts
    alerts = DATA_DIR / "alerts.jsonl"
    groups.append(("Fired alerts (poc/data/alerts.jsonl)",
                   [alerts] if alerts.exists() else []))

    # 6. Saved video snippets
    groups.append(
        ("Saved video snippets (poc/data/snippets/*.mp4)",
         _safe_glob(DATA_DIR / "snippets", "*.mp4"))
    )

    # 7. Rolling video buffer (per-camera segments)
    if not keep_buffer:
        buffer_root = DATA_DIR / "buffer"
        segments: list[Path] = []
        if buffer_root.is_dir():
            for camera_dir in sorted(buffer_root.iterdir()):
                if camera_dir.is_dir():
                    segments.extend(sorted(camera_dir.glob("segment_*")))
        groups.append(
            ("Rolling video buffer (poc/data/buffer/<cam>/segment_*)", segments)
        )

    return groups


def total_size(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            continue
    return total


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def print_summary(groups: list[tuple[str, list[Path]]]) -> int:
    grand_total = 0
    grand_count = 0
    print()
    for label, paths in groups:
        if not paths:
            print(f"  [---] {label}: nothing to clean")
            continue
        size = total_size(paths)
        grand_total += size
        grand_count += len(paths)
        print(f"  [del] {label}: {len(paths)} file(s), {fmt_size(size)}")
        # Show up to 3 example paths so the operator sees what's targeted
        for p in paths[:3]:
            print(f"          {p.relative_to(POC_DIR)}")
        if len(paths) > 3:
            print(f"          ... and {len(paths) - 3} more")
    print(f"\n  Total: {grand_count} file(s), {fmt_size(grand_total)}\n")
    return grand_count


def confirm() -> bool:
    try:
        ans = input("Delete the above? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in {"y", "yes"}


def delete_all(groups: list[tuple[str, list[Path]]]) -> tuple[int, int]:
    deleted = 0
    failed = 0
    for _label, paths in groups:
        for p in paths:
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                deleted += 1
            except OSError as exc:
                failed += 1
                print(f"  [warn] could not delete {p}: {exc}", file=sys.stderr)
    return deleted, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Wipe POC test state for a fresh start.")
    parser.add_argument("--yes", action="store_true", help="don't prompt; just delete")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be deleted, don't actually delete")
    parser.add_argument("--keep-buffer", action="store_true",
                        help="leave the rolling video buffer alone (saves a few GB if recently active)")
    args = parser.parse_args()

    print(f"\nRLCC POC reset — target: {POC_DIR}")
    if args.dry_run:
        print("  mode: DRY RUN (no files will be deleted)")
    elif args.yes:
        print("  mode: --yes (no confirmation prompt)")
    else:
        print("  mode: interactive")
    if args.keep_buffer:
        print("  --keep-buffer: rolling video buffer will be preserved")

    groups = collect_targets(keep_buffer=args.keep_buffer)
    target_count = print_summary(groups)

    if target_count == 0:
        print("Nothing to clean. Exiting.")
        return 0

    if args.dry_run:
        print("Dry run complete. No files were touched.")
        return 0

    if not args.yes and not confirm():
        print("Aborted. No files were touched.")
        return 1

    deleted, failed = delete_all(groups)
    print(f"\nDone. Deleted {deleted} file(s); {failed} failure(s).")
    if failed:
        print("Re-run with sudo if a permission error is the cause, "
              "or stop the backend/CV services first if a file was held open.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
