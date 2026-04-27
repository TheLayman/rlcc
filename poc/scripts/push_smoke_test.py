#!/usr/bin/env python3
"""
End-to-end smoke test for the RLCC Nukkad push receiver.

Walks through every important behavior of `POST /v1/rlcc/launch-event`:
  1. /health is reachable and the backend reports its config
  2. missing auth header is rejected (401)
  3. wrong auth header is rejected (401)
  4. empty body is rejected (400 Invalid JSON)
  5. valid auth + empty {} is accepted (200)
  6. a realistic 4-event transaction (Begin / SaleLine / Payment / Commit)
     is accepted and the assembler records it
  7. /health afterwards shows last_push_event_at bumped + recent_pos_events grew

Reads the auth key from poc/.env (NUKKAD_PUSH_AUTH_KEY) by default, so on the
app server you can just run:

    python3 poc/scripts/push_smoke_test.py

Useful flags:
    --base-url http://127.0.0.1:8001    target receiver (default loopback)
    --store    NDCIN1231                CIN to use in the synthetic events
    --terminal "POS 1"                  POS terminal label
    --token    XXX                      override auth key (else read from .env)
    --keep                              don't strip the test bill from storage
    --verbose                           print full response bodies

Exits 0 if every step passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

# ANSI colors — degrade gracefully if not a TTY.
def _color(code: str) -> str:
    return code if sys.stdout.isatty() else ""

GREEN  = _color("\033[32m")
RED    = _color("\033[31m")
YELLOW = _color("\033[33m")
DIM    = _color("\033[2m")
BOLD   = _color("\033[1m")
RESET  = _color("\033[0m")


# ---------------------------------------------------------------------------- helpers

def load_dotenv_token() -> tuple[str, str]:
    """Look for poc/.env relative to this script. Return (token, source)."""
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / ".env",
        Path.cwd() / "poc" / ".env",
        Path.cwd() / ".env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "NUKKAD_PUSH_AUTH_KEY":
                    v = v.strip().strip('"').strip("'")
                    if v and not v.lower().startswith("replace-with-"):
                        return (v, str(path))
        except OSError:
            continue
    return ("", "")


def http_request(method: str, url: str, headers: dict | None = None,
                 body: bytes | None = None, timeout: float = 10.0
                 ) -> tuple[int, dict | str, dict[str, str]]:
    """Minimal HTTP helper. Returns (status, parsed_or_raw_body, headers)."""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.getcode()
            resp_headers = dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return (exc.code, _maybe_json(raw), dict(getattr(exc, "headers", {}) or {}))
    except urllib.error.URLError as exc:
        return (0, f"URLError: {exc.reason}", {})
    except TimeoutError:
        return (0, f"timeout after {timeout}s", {})
    except Exception as exc:
        return (0, f"{exc.__class__.__name__}: {exc}", {})
    return (status, _maybe_json(raw), resp_headers)


def _maybe_json(raw: str):
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


# ---------------------------------------------------------------------------- test runner

@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str
    body_summary: str = ""


class Runner:
    def __init__(self, base_url: str, token: str, store: str, terminal: str,
                 verbose: bool = False):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.store = store
        self.terminal = terminal
        self.verbose = verbose
        self.results: list[StepResult] = []
        self.bill_number = f"SMOKE-{uuid.uuid4().hex[:8].upper()}"

    # -- step helpers ------------------------------------------------------

    def _post(self, path: str, headers: dict, body: dict | None,
              raw_body: bytes | None = None):
        url = self.base_url + path
        data = raw_body if raw_body is not None else (
            json.dumps(body).encode("utf-8") if body is not None else b""
        )
        return http_request("POST", url, headers=headers, body=data)

    def _record(self, name: str, ok: bool, detail: str, body):
        body_summary = ""
        if self.verbose and body not in ("", None):
            try:
                body_summary = json.dumps(body, indent=2)
            except (TypeError, ValueError):
                body_summary = str(body)
        elif isinstance(body, dict):
            msg = body.get("message") or body.get("status")
            if msg is not None:
                body_summary = json.dumps(body)[:160]
        elif isinstance(body, str) and body:
            body_summary = body[:160]
        self.results.append(StepResult(name, ok, detail, body_summary))
        tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{tag}] {name} — {detail}")
        if body_summary and (self.verbose or not ok):
            for line in body_summary.splitlines():
                print(f"        {DIM}{line}{RESET}")

    # -- individual checks -------------------------------------------------

    def step_health_initial(self) -> dict | str:
        status, body, _ = http_request("GET", self.base_url + "/health")
        ok = status == 200 and isinstance(body, dict) and body.get("status") == "ok"
        backend = body.get("backend", {}) if isinstance(body, dict) else {}
        cfg = body.get("config", {}) if isinstance(body, dict) else {}
        detail = (
            f"HTTP {status} - stores={cfg.get('store_count')} cameras={cfg.get('camera_count')} "
            f"recent_pos_events={backend.get('recent_pos_events')} "
            f"last_push={backend.get('last_push_event_at')}"
        )
        self._record("health (baseline)", ok, detail, body)
        return body if ok else {}

    def step_no_auth(self):
        status, body, _ = self._post(
            "/v1/rlcc/launch-event",
            headers={"Content-Type": "application/json"},
            body={},
        )
        ok = status == 401
        self._record("POST without auth header → 401",
                     ok, f"HTTP {status}", body)

    def step_wrong_auth(self):
        status, body, _ = self._post(
            "/v1/rlcc/launch-event",
            headers={
                "Content-Type": "application/json",
                "x-authorization-key": "definitely-not-the-key",
            },
            body={},
        )
        ok = status == 401
        self._record("POST with wrong auth → 401",
                     ok, f"HTTP {status}", body)

    def step_empty_body(self):
        status, body, _ = self._post(
            "/v1/rlcc/launch-event",
            headers={
                "Content-Type": "application/json",
                "x-authorization-key": self.token,
            },
            body=None,  # truly empty body, not even '{}'
            raw_body=b"",
        )
        # Receiver returns 400 on empty / invalid JSON.
        ok = status == 400
        self._record("POST empty body → 400 Invalid JSON",
                     ok, f"HTTP {status}", body)

    def step_empty_json(self):
        status, body, _ = self._post(
            "/v1/rlcc/launch-event",
            headers={
                "Content-Type": "application/json",
                "x-authorization-key": self.token,
            },
            body={},
        )
        ok = status == 200
        self._record("POST '{}' with auth → 200",
                     ok, f"HTTP {status}", body)

    def step_real_transaction(self):
        """Push a 4-event transaction Begin → SaleLine → Payment → Commit."""
        now = datetime.now(IST).replace(microsecond=0)
        ts = now.isoformat()
        bill = self.bill_number
        # Nukkad ties every event in a transaction together via
        # `transactionSessionId`. The assembler's begin() requires it as a
        # hard key, so all four events must share the same value.
        session_id = f"SMOKE-SESSION-{bill}"

        common = {
            "storeIdentifier": self.store,
            "posTerminalNo": self.terminal,
            "transactionSessionId": session_id,
            "transactionNumber": bill,
            "billNumber": bill,
        }

        events = [
            {
                **common,
                "event": "BeginTransactionWithTillLookup",
                "cashier": "smoke-test",
                "transactionTimeStamp": ts,
                "transactionType": "CompletedNormally",
            },
            {
                **common,
                "event": "AddTransactionSaleLine",
                "lineNumber": 1,
                "itemId": "SMOKE-ITEM",
                "itemDescription": "Smoke Test Donut",
                "itemQuantity": 1,
                "itemUnitPrice": 99.0,
                "totalAmount": 99.0,
                "scanAttribute": "None",
                "itemAttribute": "None",
                "discountType": "NoLineDiscount",
                "lineTimeStamp": ts,
            },
            {
                **common,
                "event": "AddTransactionPaymentLine",
                "lineNumber": 1,
                "lineAttribute": "Cash",
                "paymentDescription": "Cash",
                "amount": 99.0,
                "lineTimeStamp": ts,
            },
            {
                **common,
                "event": "CommitTransaction",
                "transactionTimeStamp": ts,
            },
        ]

        all_ok = True
        for idx, payload in enumerate(events, start=1):
            status, body, _ = self._post(
                "/v1/rlcc/launch-event",
                headers={
                    "Content-Type": "application/json",
                    "x-authorization-key": self.token,
                },
                body=payload,
            )
            step_ok = status == 200
            all_ok &= step_ok
            ev = payload["event"]
            self._record(f"  event {idx}/4 — {ev}",
                         step_ok, f"HTTP {status}", body)

        # Quick summary line for the whole transaction.
        self.results.append(StepResult(
            f"realistic txn {bill}",
            all_ok,
            "all 4 events accepted" if all_ok else "one or more events failed",
        ))

    def step_health_final(self, baseline_health: dict):
        status, body, _ = http_request("GET", self.base_url + "/health")
        if status != 200 or not isinstance(body, dict):
            self._record("health (post-test)", False, f"HTTP {status}", body)
            return
        backend = body.get("backend", {})
        last_push = backend.get("last_push_event_at")
        recent = backend.get("recent_pos_events")
        baseline_recent = (baseline_health.get("backend") or {}).get("recent_pos_events", 0)
        ok = bool(last_push) and (recent or 0) > (baseline_recent or 0)
        detail = (
            f"last_push_event_at={last_push!r}  recent_pos_events="
            f"{baseline_recent} → {recent}"
        )
        self._record("health (post-test)", ok, detail, body)

    # -- driver ------------------------------------------------------------

    def run(self) -> int:
        print(f"{BOLD}RLCC push receiver smoke test{RESET}")
        print(f"  base url     : {self.base_url}")
        print(f"  store        : {self.store}  terminal: {self.terminal!r}")
        print(f"  bill number  : {self.bill_number}")
        print(f"  token        : {self.token[:6]}…{self.token[-2:]}  (len={len(self.token)})")
        print()

        baseline = self.step_health_initial() or {}
        if not baseline:
            print(f"\n{RED}Backend unreachable — aborting further checks.{RESET}")
            return 1

        # Verify the configured store actually exists in this backend.
        cfg_count = (baseline.get("config") or {}).get("store_count")
        if cfg_count is not None:
            stores_url = self.base_url + "/api/stores"
            s_status, s_body, _ = http_request("GET", stores_url)
            store_ids = []
            if s_status == 200 and isinstance(s_body, list):
                store_ids = [s.get("cin") for s in s_body if isinstance(s, dict)]
            elif s_status == 200 and isinstance(s_body, dict):
                store_ids = [s.get("cin") for s in (s_body.get("stores") or [])
                             if isinstance(s, dict)]
            if store_ids and self.store not in store_ids:
                print(f"  {YELLOW}warn: store {self.store} not in /api/stores "
                      f"({', '.join(store_ids)}). Push will be accepted but won't "
                      f"map to a configured store.{RESET}\n")

        self.step_no_auth()
        self.step_wrong_auth()
        self.step_empty_body()
        self.step_empty_json()
        self.step_real_transaction()
        # Small delay so /health snapshot reflects the newest events.
        time.sleep(0.5)
        self.step_health_final(baseline)

        passed = sum(1 for r in self.results if r.ok)
        total  = len(self.results)
        print()
        print(f"{BOLD}Summary:{RESET} {passed}/{total} checks passed")
        if passed != total:
            print(f"\n{RED}Failures:{RESET}")
            for r in self.results:
                if not r.ok:
                    print(f"  - {r.name}: {r.detail}")
            return 1
        print(f"{GREEN}All good — push path is healthy end-to-end.{RESET}")
        return 0


# ---------------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description="RLCC Nukkad push receiver smoke test")
    parser.add_argument("--base-url", default=os.getenv("RLCC_BASE_URL", "http://127.0.0.1:8001"),
                        help="Receiver base URL (default http://127.0.0.1:8001)")
    parser.add_argument("--store", default=os.getenv("RLCC_TEST_STORE", "NDCIN1231"),
                        help="Store CIN to use for the synthetic transaction (default NDCIN1231)")
    parser.add_argument("--terminal", default=os.getenv("RLCC_TEST_TERMINAL", "POS 1"),
                        help='POS terminal label (default "POS 1")')
    parser.add_argument("--token", default="",
                        help="Auth key (else read NUKKAD_PUSH_AUTH_KEY from env or poc/.env)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print full response bodies for every step")
    args = parser.parse_args()

    token = args.token or os.getenv("NUKKAD_PUSH_AUTH_KEY", "")
    source = "--token" if args.token else ("env" if token else "")
    if not token:
        token, source = load_dotenv_token()
    if not token:
        print(f"{RED}No auth key found.{RESET} Set NUKKAD_PUSH_AUTH_KEY in poc/.env, "
              f"export it, or pass --token.")
        return 2
    if source and "env" not in source.lower():
        print(f"{DIM}token loaded from {source}{RESET}")

    runner = Runner(args.base_url, token, args.store, args.terminal, verbose=args.verbose)
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
