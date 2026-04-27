#!/usr/bin/env python3
"""
Verify the 9 RLCC push API endpoints.

Sends a minimal valid payload to each of the 9 endpoints and reports which
return 200 OK and which fail. Use this on the server after deploying the
backend to confirm the integration surface that Nukkad pushes to.

Stdlib only — no pip installs required.

Usage:
    python3 poc/scripts/verify_push_endpoints.py
    python3 poc/scripts/verify_push_endpoints.py --base-url http://localhost:8001 --auth-key test
    BASE_URL=http://localhost:8001 NUKKAD_PUSH_AUTH_KEY=test python3 poc/scripts/verify_push_endpoints.py

Environment variables (used as defaults):
    BASE_URL                 default http://localhost:8001
    NUKKAD_PUSH_AUTH_KEY     default test  (matches poc/.env.example default)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _load_dotenv_auth_key() -> tuple[str, str]:
    """Look for poc/.env and return (NUKKAD_PUSH_AUTH_KEY, source_path) if found."""
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
                if k.strip() != "NUKKAD_PUSH_AUTH_KEY":
                    continue
                v = v.strip().strip('"').strip("'")
                if v:
                    return (v, str(path))
        except OSError:
            continue
    return ("", "")


# (path, event name, payload_template). Each template is filled at runtime with
# the shared transactionSessionId so the endpoints share a synthetic transaction
# end-to-end (Begin -> Sale -> Payment -> Total -> Commit), with GetTill and
# BillReprint exercised independently.
ENDPOINTS: list[tuple[str, str, dict]] = [
    (
        "/v1/rlcc/begin-transaction-with-till-lookup",
        "BeginTransactionWithTillLookup",
        {
            "applicationType": "Retail",
            "storeIdentifier": "VERIFY-STORE",
            "posTerminalNo": "VERIFY-POS",
            "isForTillLookup": True,
            "isPreviousTransaction": False,
            "branch": "verify-branch",
            "tillDescription": "POS1",
            "transactionNumber": "VERIFY-TXN-0001",
            "currencyCode": "INR",
            "transactionType": "CompletedNormally",
            "employeePurchase": False,
        },
    ),
    (
        "/v1/rlcc/add-transaction-event",
        "AddTransactionEvent",
        {
            "applicationType": "Retail",
            "storeIdentifier": "VERIFY-STORE",
            "posTerminalNo": "VERIFY-POS",
            "lineNumber": 1,
            "lineAttribute": "None",
            "eventDescription": "verify event",
            "printable": False,
        },
    ),
    (
        "/v1/rlcc/add-transaction-sale-line",
        "AddTransactionSaleLine",
        {
            "applicationType": "Retail",
            "storeIdentifier": "VERIFY-STORE",
            "posTerminalNo": "VERIFY-POS",
            "isForTillLookup": True,
            "isPreviousTransaction": False,
            "lineNumber": 1,
            "itemAttribute": "None",
            "scanAttribute": "Auto",
            "itemDescription": "Verify Item",
            "itemQuantity": 1,
            "itemUnitPrice": 100.0,
            "discountType": "NoLineDiscount",
            "totalAmount": 100.0,
            "printable": True,
        },
    ),
    (
        "/v1/rlcc/add-transaction-sale-line-with-till-lookup",
        "AddTransactionSaleLineWithTillLookup",
        {
            "applicationType": "Retail",
            "storeIdentifier": "VERIFY-STORE",
            "posTerminalNo": "VERIFY-POS",
            "isForTillLookup": True,
            "isPreviousTransaction": False,
            "lineNumber": 2,
            "itemAttribute": "None",
            "scanAttribute": "Auto",
            "itemDescription": "Verify Item 2",
            "itemQuantity": 1,
            "itemUnitPrice": 50.0,
            "discountType": "NoLineDiscount",
            "totalAmount": 50.0,
            "printable": True,
        },
    ),
    (
        "/v1/rlcc/add-transaction-payment-line",
        "AddTransactionPaymentLine",
        {
            "applicationType": "Retail",
            "storeIdentifier": "VERIFY-STORE",
            "posTerminalNo": "VERIFY-POS",
            "lineNumber": 1,
            "lineAttribute": "Cash",
            "paymentDescription": "Cash",
            "amount": 150.0,
            "printable": True,
        },
    ),
    (
        "/v1/rlcc/add-transaction-total-line",
        "AddTransactionTotalLine",
        {
            "applicationType": "Retail",
            "storeIdentifier": "VERIFY-STORE",
            "posTerminalNo": "VERIFY-POS",
            "lineNumber": 1,
            "lineAttribute": "TotalAmountToBePaid",
            "totalDescription": "Total amount to be paid",
            "amount": 150.0,
            "printable": True,
        },
    ),
    (
        "/v1/rlcc/commit-transaction",
        "CommitTransaction",
        {
            "applicationType": "Retail",
            "storeIdentifier": "VERIFY-STORE",
            "posTerminalNo": "VERIFY-POS",
            "transactionNumber": "VERIFY-BILL-0001",
        },
    ),
    (
        "/v1/rlcc/get-till",
        "GetTill",
        {
            "applicationType": "Retail",
            "storeIdentifier": "VERIFY-STORE",
            "posTerminalNo": "VERIFY-POS",
            "branch": "verify-branch",
            "tillDescription": "POS1",
        },
    ),
    (
        "/v1/rlcc/bill-reprint",
        "BillReprint",
        {
            "applicationType": "Retail",
            "storeIdentifier": "VERIFY-STORE",
            "posTerminalNo": "VERIFY-POS",
            "branch": "verify-branch",
            "tillDescription": "POS1",
            "transactionTimestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "billNumber": "VERIFY-BILL-0001",
            "cashier": "VERIFY-CASHIER",
        },
    ),
]


def post_json(url: str, headers: dict, body: dict, timeout: float = 10.0) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return (resp.getcode(), raw[:300])
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return (exc.code, raw[:300])
    except urllib.error.URLError as exc:
        return (0, f"URLError: {exc.reason}")
    except socket.timeout:
        return (0, f"timeout after {timeout}s")
    except Exception as exc:
        return (0, f"{exc.__class__.__name__}: {exc}")


def pad(s: str, n: int) -> str:
    s = str(s)
    return s + " " * (n - len(s)) if len(s) < n else s[: n - 1] + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the 9 RLCC push endpoints")
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "http://localhost:8001"),
        help="Backend base URL (default: $BASE_URL or http://localhost:8001)",
    )
    env_key = os.getenv("NUKKAD_PUSH_AUTH_KEY", "")
    dotenv_key, dotenv_source = ("", "")
    if not env_key:
        dotenv_key, dotenv_source = _load_dotenv_auth_key()
    parser.add_argument(
        "--auth-key",
        default=env_key or dotenv_key or "test",
        help="x-authorization-key header value (default: $NUKKAD_PUSH_AUTH_KEY, then poc/.env, then 'test')",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout in seconds (default 10)",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    headers = {"x-authorization-key": args.auth_key, "Content-Type": "application/json"}

    # Use a unique session id per run so the receiver's dedupe doesn't swallow our calls.
    session_id = f"verify-{uuid.uuid4()}"

    auth_source = (
        "argv" if env_key == "" and dotenv_key == "" and args.auth_key not in ("", "test")
        else "$NUKKAD_PUSH_AUTH_KEY" if env_key
        else f"{dotenv_source}" if dotenv_key
        else "fallback 'test'"
    )

    print(f"\nRLCC push endpoint verifier")
    print(f"  base_url : {base}")
    print(f"  auth_key : {'(set)' if args.auth_key else '(empty)'}  [from {auth_source}]")
    print(f"  session  : {session_id}")
    print(f"  ts       : {datetime.now().isoformat(timespec='seconds')}\n")

    results: list[tuple[str, str, int, int, str]] = []
    for path, event, payload_template in ENDPOINTS:
        payload = dict(payload_template)
        payload["event"] = event
        payload["transactionSessionId"] = session_id
        url = f"{base}{path}"

        started = time.monotonic()
        status, body = post_json(url, headers, payload, timeout=args.timeout)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        results.append((path, event, status, elapsed_ms, body))

        tag = "OK  " if status == 200 else ("AUTH" if status == 401 else "FAIL")
        print(f"  [{tag}] {pad(path, 56)} {status:>3}  {elapsed_ms:>4}ms")

    print("\nSUMMARY")
    print("-" * 78)
    ok = [r for r in results if r[2] == 200]
    bad = [r for r in results if r[2] != 200]
    print(f"  passed: {len(ok)} / {len(results)}")
    if bad:
        print(f"  failed: {len(bad)}")
        for path, event, status, _ms, body in bad:
            snippet = body.replace("\n", " ")[:120]
            print(f"    - {pad(path, 56)} {event}  status={status}  body={snippet}")

    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
