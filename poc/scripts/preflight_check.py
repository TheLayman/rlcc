#!/usr/bin/env python3
"""
Preflight check for RLCC POC stores.

For each store:
  1. RTSP stream reachability
       - TCP connect to camera IP:554
       - ffprobe probe (if ffmpeg is installed)
  2. Sales API variant detection
       - POSTs to BOTH F&B and Retail endpoints with a short time window
       - Reports which one returns a valid response + bill count

Usage:
    # Easiest — auto-loads poc/.env (EXTERNAL_SALES_HEADER_TOKEN):
    python3 poc/scripts/preflight_check.py

    # Or pass explicitly:
    SALES_API_TOKEN=xxxxx python3 poc/scripts/preflight_check.py
    python3 poc/scripts/preflight_check.py --token xxxxx
    python3 poc/scripts/preflight_check.py --skip-sales   # RTSP only
    python3 poc/scripts/preflight_check.py --skip-rtsp    # Sales API only

Requires: python3 (stdlib only), optionally ffprobe in PATH for deep RTSP probe.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

SALES_API_ENDPOINTS = {
    "F&B":    "https://integrations.fnb.posifly.in/v1/sales/getSalesWithItems",
    "Retail": "https://integrations.retail.posifly.in/v1/sales/getSalesWithItems",
}


@dataclass
class Store:
    sno: int
    cin: str
    name: str
    location: str
    application: str  # "Dino" -> F&B; "Retail" -> Retail
    category: str
    camera_ip: str = ""
    rtsp_url: str = ""

    @property
    def expected_variant(self) -> str:
        return "F&B" if self.application.strip().lower() == "dino" else "Retail"


STORES: list[Store] = [
    Store(1,  "NDCIN1231",  "Nizami Dawat",        "Aero Plaza",       "Dino",   "F&B - QSR",
          camera_ip="10.86.158.25",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.25:554/videoStreamId=3"),
    Store(2,  "NDCIN1422",  "Cafe Niloufer",       "Airport Village",  "Dino",   "F&B - QSR & Dine In",
          camera_ip="10.86.158.163",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.163/rtsp/defaultPrimary?streamType=u"),
    Store(3,  "NSCIN10323", "Pulla Reddy Sweets",  "Retail Village",   "Retail", "Retail - Packaged Food",
          camera_ip="10.86.158.71",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.71:554/videoStreamId=2"),
    Store(4,  "NSCIN8244",  "Enwrap",              "Fore Court",       "Retail", "Services - Baggage Wrapping",
          camera_ip="10.86.158.140",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.140/rtsp/defaultPrimary?streamType=u"),
    Store(5,  "NSCIN10489", "Killer Jeans",        "Retail Village",   "Retail", "Retail - Apparels",
          camera_ip="10.86.157.88",
          rtsp_url="rtsp://10.86.157.88/media/video1"),
    Store(6,  "NDCIN2082",  "Krispy Kreme",        "Aero Plaza",       "Dino",   "F&B - Bakery",
          camera_ip="10.86.158.196",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.196/rtsp/defaultPrimary?streamType=u"),
    Store(7,  "NSCIN8260",  "Karachi Bakery",      "Airport Village",  "Retail", "Retail - Bakery & Packed Food",
          camera_ip="10.86.158.172",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.172/rtsp/defaultPrimary?streamType=u"),
    Store(8,  "NDCIN2123",  "Visitor Gallery",     "Fore Court",       "Dino",   "Services - Airport Entry Ticket",
          camera_ip="10.86.158.168",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.168/rtsp/defaultPrimary?streamType=u"),
    Store(9,  "NSCIN10697", "Relaxo",              "Arrivals",         "Retail", "Services - Massage Chair",
          camera_ip="10.86.158.179",
          rtsp_url="rtsp://kspoc:kspoc123@10.86.158.179/rtsp/defaultPrimary?streamType=u"),
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


def post_json(url: str, headers: dict, body: dict, timeout: float = 20.0) -> tuple[int, dict | None, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return (exc.code, None, raw[:300])
    except urllib.error.URLError as exc:
        return (0, None, f"URLError: {exc.reason}")
    except socket.timeout:
        return (0, None, f"timeout after {timeout}s")
    except Exception as exc:
        return (0, None, f"{exc.__class__.__name__}: {exc}")

    try:
        return (status, json.loads(raw), "")
    except json.JSONDecodeError:
        return (status, None, (raw or "")[:300])


def sales_api_check(store: Store, tokens: dict[str, str], hours: int) -> dict[str, dict]:
    now = datetime.now(IST)
    start = now - timedelta(hours=hours)
    body = {
        "cin": store.cin,
        "from": str(int(start.timestamp())),
        "to": str(int(now.timestamp())),
        "pageNo": "1",
    }

    results: dict[str, dict] = {}
    for variant, url in SALES_API_ENDPOINTS.items():
        token = tokens.get(variant, "")
        if not token:
            results[variant] = {
                "status": 0, "elapsed_ms": 0,
                "error": f"no token configured for {variant} (skipped)",
                "skipped": True,
            }
            continue

        started = time.monotonic()
        status, payload, err = post_json(url, {"X-Nukkad-API-Token": token}, body)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        result: dict = {"status": status, "elapsed_ms": elapsed_ms, "error": err}
        if payload is not None:
            result["response_flag"] = payload.get("response")
            data = payload.get("data") or {}
            bills = data.get("bills") or []
            result["bill_count"] = len(bills)
            result["page_count"] = data.get("pageCount")
            if "message" in payload:
                result["message"] = payload.get("message")
        results[variant] = result
    return results


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


def _placeholder(v: str) -> bool:
    return v.lower().startswith("replace-with-")


def load_dotenv_tokens() -> tuple[dict[str, str], str]:
    """Look for poc/.env (or ./.env). Return ({variant: token}, source_path).

    Variant keys: 'F&B' and 'Retail'.
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / ".env",
        Path.cwd() / "poc" / ".env",
        Path.cwd() / ".env",
    ]
    fnb_keys    = ("EXTERNAL_SALES_HEADER_TOKEN", "SALES_API_TOKEN", "NUKKAD_API_TOKEN")
    retail_keys = ("EXTERNAL_SALES_RETAIL_HEADER_TOKEN", "SALES_API_TOKEN_RETAIL")

    for path in candidates:
        if not path.is_file():
            continue
        tokens: dict[str, str] = {}
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if not v or _placeholder(v):
                    continue
                if k in fnb_keys and "F&B" not in tokens:
                    tokens["F&B"] = v
                elif k in retail_keys and "Retail" not in tokens:
                    tokens["Retail"] = v
        except OSError:
            continue
        if tokens:
            return (tokens, str(path))
    return ({}, "")


def main() -> int:
    parser = argparse.ArgumentParser(description="RLCC POC preflight check")

    env_fnb    = os.getenv("SALES_API_TOKEN") or os.getenv("EXTERNAL_SALES_HEADER_TOKEN") or ""
    env_retail = os.getenv("SALES_API_TOKEN_RETAIL") or os.getenv("EXTERNAL_SALES_RETAIL_HEADER_TOKEN") or ""
    dotenv_tokens, env_source = ({}, "")
    if not (env_fnb and env_retail):
        dotenv_tokens, env_source = load_dotenv_tokens()

    parser.add_argument("--token", "--token-fnb", dest="token_fnb",
                        default=env_fnb or dotenv_tokens.get("F&B", ""),
                        help="F&B (Posifly) token. Else env EXTERNAL_SALES_HEADER_TOKEN / SALES_API_TOKEN, or poc/.env")
    parser.add_argument("--token-retail", dest="token_retail",
                        default=env_retail or dotenv_tokens.get("Retail", ""),
                        help="Retail (Nukkad Shops) token. Else env EXTERNAL_SALES_RETAIL_HEADER_TOKEN, or poc/.env")
    parser.add_argument("--hours", type=int, default=24,
                        help="Sales API lookback window in hours (default 24)")
    parser.add_argument("--skip-rtsp", action="store_true", help="Skip RTSP checks")
    parser.add_argument("--skip-sales", action="store_true", help="Skip sales API checks")
    parser.add_argument("--only", default="", help="Comma-separated CIN list to limit the run")
    args = parser.parse_args()

    tokens: dict[str, str] = {}
    if args.token_fnb:
        tokens["F&B"] = args.token_fnb
    if args.token_retail:
        tokens["Retail"] = args.token_retail

    if args.only:
        only = {c.strip() for c in args.only.split(",") if c.strip()}
        stores = [s for s in STORES if s.cin in only]
    else:
        stores = STORES

    if not args.skip_sales and not tokens:
        print("[warn] no tokens found. Expected one or both of:")
        print("         F&B    - EXTERNAL_SALES_HEADER_TOKEN          (or --token-fnb)")
        print("         Retail - EXTERNAL_SALES_RETAIL_HEADER_TOKEN   (or --token-retail)")
        print("[warn] sales API check will be skipped.")
        args.skip_sales = True

    print_header(f"RLCC POC preflight  ({len(stores)} stores)  run at {datetime.now().isoformat(timespec='seconds')}")
    print(f"ffprobe: {'found at ' + (shutil.which('ffprobe') or '') if shutil.which('ffprobe') else 'NOT FOUND (pip/apt install ffmpeg for deep probe)'}")
    if tokens:
        parts = []
        for variant in ("F&B", "Retail"):
            if variant in tokens:
                parts.append(variant)
        source = f" (loaded from {env_source})" if env_source else ""
        missing = [v for v in ("F&B", "Retail") if v not in tokens]
        suffix = f"  missing: {','.join(missing)}" if missing else ""
        print(f"tokens : {', '.join(parts)}{source}{suffix}")

    rtsp_rows: list[tuple[Store, dict]] = []
    sales_rows: list[tuple[Store, dict[str, dict]]] = []

    for store in stores:
        print_header(f"[{store.sno}] {store.cin}  {store.name}  ({store.application} / {store.location})")

        if not args.skip_rtsp:
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

        if not args.skip_sales:
            sales = sales_api_check(store, tokens, args.hours)
            sales_rows.append((store, sales))
            print(f"  Sales API  (window: last {args.hours}h, expected variant: {store.expected_variant})")
            for variant, r in sales.items():
                status = r.get("status")
                if r.get("skipped"):
                    tag = "SKIP"
                    detail = r.get("error") or "no token"
                elif status == 200 and r.get("response_flag"):
                    tag = "OK  "
                    detail = f"response=true  bills={r.get('bill_count')}  pageCount={r.get('page_count')}"
                elif status == 200:
                    tag = "EMPTY"
                    detail = f"response={r.get('response_flag')!r}  bills={r.get('bill_count', 0)}"
                    if r.get("message"):
                        detail += f"  message={r['message']!r}"
                elif status == 0:
                    tag = "FAIL"
                    detail = f"no response ({r.get('error') or 'unknown'})"
                else:
                    tag = "FAIL"
                    detail = f"HTTP {status}  {r.get('error') or ''}".strip()
                print(f"    {pad(variant, 8)} [{tag}] {detail}  ({r['elapsed_ms']} ms)")

    print_header("SUMMARY")
    if rtsp_rows:
        print("RTSP reachability:")
        for store, r in rtsp_rows:
            if r.get("skipped"):
                line = "SKIP (no URL/IP)"
            else:
                tcp = "tcp=OK" if r["tcp_ok"] else "tcp=FAIL"
                prob = "probe=OK" if r["probe_ok"] else "probe=FAIL"
                extra = ""
                if not r["probe_ok"]:
                    # Truncate the multi-transport error string so the row stays readable.
                    msg = r.get("probe_msg", "") or ""
                    extra = f"  | {msg[:140]}" + ("..." if len(msg) > 140 else "")
                elif r["probe_ok"]:
                    extra = f"  | {r.get('probe_msg','')}"
                line = f"{tcp}  {prob}{extra}"
            print(f"  [{pad(store.cin, 11)}] {pad(store.name, 22)} {line}")
    if sales_rows:
        print("\nSales API which variant returned data:")
        for store, variants in sales_rows:
            hits = [v for v, r in variants.items()
                    if r.get("status") == 200 and r.get("response_flag") and (r.get("bill_count") or 0) > 0]
            ok_empty = [v for v, r in variants.items()
                        if r.get("status") == 200 and r.get("response_flag") and not (r.get("bill_count") or 0)]
            auth = [v for v, r in variants.items() if r.get("status") == 200]
            if hits:
                verdict = f"DATA from {','.join(hits)}"
            elif ok_empty:
                verdict = f"AUTH OK (no bills in window) from {','.join(ok_empty)}"
            elif auth:
                verdict = f"HTTP 200 but response=false from {','.join(auth)}"
            else:
                verdict = "NO VARIANT ACCEPTED REQUEST"
            print(f"  [{pad(store.cin, 11)}] {pad(store.name, 22)} expected={pad(store.expected_variant, 6)} -> {verdict}")

    print("\nDone. Paste this entire output back into chat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
