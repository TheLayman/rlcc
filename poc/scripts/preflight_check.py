#!/usr/bin/env python3
"""
Preflight check for RLCC POC stores.

For each store, verifies RTSP stream reachability:
  1. TCP connect to camera IP:554
  2. ffprobe probe (if ffmpeg is installed)

Usage:
    python3 poc/scripts/preflight_check.py
    python3 poc/scripts/preflight_check.py --only NDCIN1231,NDCIN1422

Requires: python3 (stdlib only), optionally ffprobe in PATH for deep RTSP probe.
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Store:
    sno: int
    cin: str
    name: str
    location: str
    application: str
    category: str
    camera_ip: str = ""
    rtsp_url: str = ""


STORES: list[Store] = [
    Store(1,  "NDCIN1231",  "Nizami Dawat",        "Aero Plaza",       "Dino",   "F&B - QSR",
          camera_ip="",
          rtsp_url=""),
    Store(2,  "NDCIN1422",  "Cafe Niloufer",       "Airport Village",  "Dino",   "F&B - QSR & Dine In",
          camera_ip="10.86.158.163",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.163/rtsp/defaultPrimary?streamType=u"),
    Store(6,  "NDCIN2082",  "Krispy Kreme",        "Aero Plaza",       "Dino",   "F&B - Bakery",
          camera_ip="10.86.158.196",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.196/rtsp/defaultPrimary?streamType=u"),
    Store(8,  "NDCIN2123",  "Visitor Gallery",     "Fore Court",       "Dino",   "Services - Airport Entry Ticket",
          camera_ip="10.86.158.168",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.168/rtsp/defaultPrimary?streamType=u"),
    Store(10, "NDCIN2071",  "Frank Hot Dog",       "Airport Village",  "Dino",   "F&B - QSR",
          camera_ip="10.86.158.230",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.230/rtsp/defaultPrimary?streamType=u"),
]


def parse_host_port(rtsp_url: str) -> tuple[str, int]:
    if not rtsp_url:
        return ("", 0)
    parsed = urllib.parse.urlparse(rtsp_url)
    host = parsed.hostname or ""
    port = parsed.port or 554
    return (host, port)


def tcp_check(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    if not host:
        return (False, "no host")
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (True, f"TCP {host}:{port} open")
    except socket.timeout:
        return (False, f"TCP {host}:{port} timeout after {timeout}s")
    except OSError as exc:
        return (False, f"TCP {host}:{port} {exc.__class__.__name__}: {exc}")


def _ffprobe_once(rtsp_url: str, transport: str | None, timeout: float) -> tuple[bool, str]:
    cmd = ["ffprobe", "-v", "error"]
    if transport:
        cmd += ["-rtsp_transport", transport]
    cmd += [
        "-rw_timeout", "10000000",
        "-timeout", "10000000",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,r_frame_rate",
        "-of", "json",
        "-i", rtsp_url,
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, text=True)
    except subprocess.TimeoutExpired:
        return (False, f"timeout after {timeout}s")
    except OSError as exc:
        return (False, f"spawn failed: {exc}")

    if proc.returncode != 0:
        err_lines = [line.strip() for line in (proc.stderr or "").splitlines() if line.strip()]
        msg = err_lines[-1] if err_lines else f"exit {proc.returncode}"
        return (False, msg[:200])

    try:
        info = json.loads(proc.stdout or "{}")
        streams = info.get("streams") or []
        if not streams:
            return (False, "connected but no video stream reported")
        s = streams[0]
        desc = f"{s.get('codec_name','?')} {s.get('width','?')}x{s.get('height','?')} @ {s.get('r_frame_rate','?')}"
        return (True, f"video stream: {desc}")
    except json.JSONDecodeError:
        return (True, "ok (unparseable metadata)")


def ffprobe_check(rtsp_url: str, timeout: float = 12.0) -> tuple[bool, str]:
    if not shutil.which("ffprobe"):
        return (False, "ffprobe not found in PATH (install ffmpeg to enable deep probe)")

    attempts: list[tuple[str, str]] = []
    # URL hints UDP via streamType=u; try UDP first, then TCP, then auto.
    for transport in ("udp", "tcp", None):
        ok, msg = _ffprobe_once(rtsp_url, transport, timeout)
        label = transport or "auto"
        if ok:
            return (True, f"[{label}] {msg}")
        attempts.append((label, msg))

    joined = "; ".join(f"{label}: {msg}" for label, msg in attempts)
    return (False, joined)


def redact_rtsp(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        if parsed.username:
            netloc = f"{parsed.username}:***@{netloc}"
        return urllib.parse.urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        return url


def pad(s: str, n: int) -> str:
    if len(s) >= n:
        return s[: n - 1] + "…"
    return s + " " * (n - len(s))


def print_header(title: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n{title}\n{bar}")


def run_rtsp(store: Store) -> dict:
    host, port = parse_host_port(store.rtsp_url) if store.rtsp_url else (store.camera_ip, 554)

    if not host:
        return {"skipped": True, "reason": "no RTSP URL and no camera IP provided"}

    tcp_ok, tcp_msg = tcp_check(host, port)

    probe_ok = False
    probe_msg = "skipped (RTSP URL missing)"
    if store.rtsp_url:
        if tcp_ok:
            probe_ok, probe_msg = ffprobe_check(store.rtsp_url)
        else:
            probe_msg = "skipped (TCP connect failed)"

    return {
        "host": host,
        "port": port,
        "tcp_ok": tcp_ok,
        "tcp_msg": tcp_msg,
        "probe_ok": probe_ok,
        "probe_msg": probe_msg,
        "rtsp_url": redact_rtsp(store.rtsp_url),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RLCC POC RTSP preflight check")
    parser.add_argument("--only", default="", help="Comma-separated CIN list to limit the run")
    args = parser.parse_args()

    if args.only:
        only = {c.strip() for c in args.only.split(",") if c.strip()}
        stores = [s for s in STORES if s.cin in only]
    else:
        stores = STORES

    print_header(f"RLCC POC RTSP preflight  ({len(stores)} stores)  run at {datetime.now().isoformat(timespec='seconds')}")
    print(f"ffprobe: {'found at ' + (shutil.which('ffprobe') or '') if shutil.which('ffprobe') else 'NOT FOUND (pip/apt install ffmpeg for deep probe)'}")

    rtsp_rows: list[tuple[Store, dict]] = []

    for store in stores:
        print_header(f"[{store.sno}] {store.cin}  {store.name}  ({store.application} / {store.location})")

        rtsp = run_rtsp(store)
        rtsp_rows.append((store, rtsp))
        if rtsp.get("skipped"):
            print(f"  RTSP: skipped — {rtsp['reason']}")
        else:
            tcp_tag  = "OK  " if rtsp["tcp_ok"]  else "FAIL"
            prob_tag = "OK  " if rtsp["probe_ok"] else "----"
            print(f"  RTSP url   : {rtsp['rtsp_url'] or '(none — IP only)'}")
            print(f"  TCP        : [{tcp_tag}] {rtsp['tcp_msg']}")
            print(f"  ffprobe    : [{prob_tag}] {rtsp['probe_msg']}")

    print_header("SUMMARY")
    print("RTSP reachability:")
    for store, r in rtsp_rows:
        if r.get("skipped"):
            line = "SKIP (no URL/IP)"
        else:
            tcp = "tcp=OK" if r["tcp_ok"] else "tcp=FAIL"
            prob = "probe=OK" if r["probe_ok"] else "probe=FAIL"
            extra = ""
            if not r["probe_ok"]:
                msg = r.get("probe_msg", "") or ""
                extra = f"  | {msg[:140]}" + ("..." if len(msg) > 140 else "")
            elif r["probe_ok"]:
                extra = f"  | {r.get('probe_msg','')}"
            line = f"{tcp}  {prob}{extra}"
        print(f"  [{pad(store.cin, 11)}] {pad(store.name, 22)} {line}")

    print("\nDone. Paste this entire output back into chat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
