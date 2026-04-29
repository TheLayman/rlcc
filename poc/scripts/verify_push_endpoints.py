#!/usr/bin/env python3
"""
Verify the 9 RLCC push API endpoints — scenario-based smoke tests.

Beyond the basic "do all 9 routes return 200", this script exercises auth
failures, body-format variants, event/route mismatches, dedupe behaviour,
out-of-order events, commits with no Begin, and the standalone GetTill /
BillReprint flows. Each scenario is a list of HTTP steps with an expected
status code per step.

Stdlib only — no pip installs required.

Usage:
    python3 poc/scripts/verify_push_endpoints.py
    python3 poc/scripts/verify_push_endpoints.py --base-url http://localhost:8001 --auth-key test
    python3 poc/scripts/verify_push_endpoints.py --only happy_path,wrong_auth
    python3 poc/scripts/verify_push_endpoints.py --list

Defaults:
    base_url   $BASE_URL or http://localhost:8001
    auth_key   $NUKKAD_PUSH_AUTH_KEY, then poc/.env, then 'test'

Exit code: 0 if every scenario passes, non-zero count of failures otherwise.
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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# ---------- env / dotenv loading -------------------------------------------------

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


# ---------- route table mirrors backend/receiver.py:ROUTES ----------------------

PATH_FOR: dict[str, str] = {
    "BeginTransactionWithTillLookup":         "/v1/rlcc/begin-transaction-with-till-lookup",
    "AddTransactionEvent":                    "/v1/rlcc/add-transaction-event",
    "AddTransactionPaymentLine":              "/v1/rlcc/add-transaction-payment-line",
    "AddTransactionSaleLine":                 "/v1/rlcc/add-transaction-sale-line",
    "AddTransactionSaleLineWithTillLookup":   "/v1/rlcc/add-transaction-sale-line-with-till-lookup",
    "AddTransactionTotalLine":                "/v1/rlcc/add-transaction-total-line",
    "CommitTransaction":                      "/v1/rlcc/commit-transaction",
    "GetTill":                                "/v1/rlcc/get-till",
    "BillReprint":                            "/v1/rlcc/bill-reprint",
}


# ---------- payload builders ----------------------------------------------------

def begin_payload(session_id: str, **overrides) -> dict:
    base = {
        "event": "BeginTransactionWithTillLookup",
        "applicationType": "Retail",
        "storeIdentifier": "VERIFY-STORE",
        "posTerminalNo": "VERIFY-POS",
        "isForTillLookup": True,
        "isPreviousTransaction": False,
        "transactionSessionId": session_id,
        "branch": "verify-branch",
        "tillDescription": "POS1",
        "transactionNumber": f"VERIFY-TXN-{session_id[-6:]}",
        "currencyCode": "INR",
        "transactionType": "CompletedNormally",
        "employeePurchase": False,
        "cashier": "VERIFY-CASHIER",
    }
    base.update(overrides)
    return base


def sale_payload(session_id: str, line_number: int = 1, **overrides) -> dict:
    base = {
        "event": "AddTransactionSaleLine",
        "applicationType": "Retail",
        "storeIdentifier": "VERIFY-STORE",
        "posTerminalNo": "VERIFY-POS",
        "isForTillLookup": True,
        "isPreviousTransaction": False,
        "transactionSessionId": session_id,
        "lineNumber": line_number,
        "itemAttribute": "None",
        "scanAttribute": "Auto",
        "itemDescription": f"Verify Item {line_number}",
        "itemQuantity": 1,
        "itemUnitPrice": 100.0,
        "discountType": "NoLineDiscount",
        "totalAmount": 100.0,
        "printable": True,
    }
    base.update(overrides)
    return base


def payment_payload(session_id: str, amount: float = 100.0, **overrides) -> dict:
    base = {
        "event": "AddTransactionPaymentLine",
        "applicationType": "Retail",
        "storeIdentifier": "VERIFY-STORE",
        "posTerminalNo": "VERIFY-POS",
        "transactionSessionId": session_id,
        "lineNumber": 1,
        "lineAttribute": "Cash",
        "paymentDescription": "Cash",
        "amount": amount,
        "printable": True,
    }
    base.update(overrides)
    return base


def total_payload(session_id: str, amount: float = 100.0, **overrides) -> dict:
    base = {
        "event": "AddTransactionTotalLine",
        "applicationType": "Retail",
        "storeIdentifier": "VERIFY-STORE",
        "posTerminalNo": "VERIFY-POS",
        "transactionSessionId": session_id,
        "lineNumber": 1,
        "lineAttribute": "TotalAmountToBePaid",
        "totalDescription": "Total amount to be paid",
        "amount": amount,
        "printable": True,
    }
    base.update(overrides)
    return base


def commit_payload(session_id: str, bill_number: str | None = None, **overrides) -> dict:
    base = {
        "event": "CommitTransaction",
        "applicationType": "Retail",
        "storeIdentifier": "VERIFY-STORE",
        "posTerminalNo": "VERIFY-POS",
        "transactionSessionId": session_id,
        "transactionNumber": bill_number or f"VERIFY-BILL-{session_id[-6:]}",
    }
    base.update(overrides)
    return base


def get_till_payload(**overrides) -> dict:
    base = {
        "event": "GetTill",
        "applicationType": "Retail",
        "storeIdentifier": "VERIFY-STORE",
        "posTerminalNo": "VERIFY-POS",
        "branch": "verify-branch",
        "tillDescription": "POS1",
    }
    base.update(overrides)
    return base


def bill_reprint_payload(bill_number: str, **overrides) -> dict:
    base = {
        "event": "BillReprint",
        "applicationType": "Retail",
        "storeIdentifier": "VERIFY-STORE",
        "posTerminalNo": "VERIFY-POS",
        "branch": "verify-branch",
        "tillDescription": "POS1",
        "transactionTimestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "billNumber": bill_number,
        "cashier": "VERIFY-CASHIER",
    }
    base.update(overrides)
    return base


def add_event_payload(session_id: str, attribute: str = "TransactionSuspended", **overrides) -> dict:
    base = {
        "event": "AddTransactionEvent",
        "applicationType": "Retail",
        "storeIdentifier": "VERIFY-STORE",
        "posTerminalNo": "VERIFY-POS",
        "transactionSessionId": session_id,
        "lineNumber": 99,
        "lineAttribute": attribute,
        "eventDescription": f"verify {attribute}",
        "printable": False,
    }
    base.update(overrides)
    return base


# ---------- HTTP --------------------------------------------------------------

def post(url: str, headers: dict, body: bytes, timeout: float = 10.0) -> tuple[int, str, dict | None]:
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        status = exc.code
    except urllib.error.URLError as exc:
        return (0, f"URLError: {exc.reason}", None)
    except socket.timeout:
        return (0, f"timeout after {timeout}s", None)
    except Exception as exc:
        return (0, f"{exc.__class__.__name__}: {exc}", None)

    try:
        return (status, raw[:400], json.loads(raw))
    except json.JSONDecodeError:
        return (status, raw[:400], None)


# ---------- step / scenario model --------------------------------------------

@dataclass
class Step:
    label: str
    path: str                        # endpoint path
    payload: dict | str              # dict → normal JSON, str → sent verbatim
    expected_status: int             # 0 means "any 2xx"
    auth_key: str | None = None      # None → use the run-wide key, "" → omit header
    expect_message_contains: str | None = None
    expect_data_keys: tuple[str, ...] | None = None


@dataclass
class Scenario:
    key: str            # short id for --only filtering
    name: str
    description: str
    steps_factory: Callable[[str], list[Step]]   # called with a per-scenario session id


# ---------- scenario library --------------------------------------------------

def _happy_path(sid: str) -> list[Step]:
    return [
        Step("Begin (envelope has TransactionSessionId)",
             PATH_FOR["BeginTransactionWithTillLookup"], begin_payload(sid), 200,
             expect_data_keys=("ErrorCode", "Succeeded", "TransactionSessionId")),
        Step("Sale 1",      PATH_FOR["AddTransactionSaleLine"],               sale_payload(sid, 1, totalAmount=100.0), 200),
        Step("Sale 2",      PATH_FOR["AddTransactionSaleLine"],               sale_payload(sid, 2, itemDescription="Item 2", totalAmount=50.0), 200),
        Step("Payment",     PATH_FOR["AddTransactionPaymentLine"],            payment_payload(sid, amount=150.0),  200),
        Step("Total",       PATH_FOR["AddTransactionTotalLine"],              total_payload(sid, amount=150.0),    200),
        Step("Commit",      PATH_FOR["CommitTransaction"],                    commit_payload(sid, "VERIFY-BILL-HP1"), 200),
    ]


def _fractional_qty(sid: str) -> list[Step]:
    # 1.250 kg item — used to crash int() coercion before models.py was fixed.
    return [
        Step("Begin", PATH_FOR["BeginTransactionWithTillLookup"], begin_payload(sid), 200),
        Step("Sale (1.250 kg)", PATH_FOR["AddTransactionSaleLine"],
             sale_payload(sid, 1, itemQuantity=1.250, itemUnitPrice=80.0, totalAmount=100.0,
                          itemDescription="Onions (kg)"), 200),
        Step("Commit", PATH_FOR["CommitTransaction"], commit_payload(sid, "VERIFY-BILL-KG"), 200),
    ]


def _stringified_bool(sid: str) -> list[Step]:
    # "false" as a string used to flip employeePurchase to True.
    return [
        Step("Begin (employeePurchase=\"false\" string)",
             PATH_FOR["BeginTransactionWithTillLookup"],
             begin_payload(sid, employeePurchase="false", isPreviousTransaction="false"), 200,
             expect_data_keys=("TransactionSessionId",)),
        Step("Commit", PATH_FOR["CommitTransaction"], commit_payload(sid, "VERIFY-BILL-BOOL"), 200),
    ]


def _sale_line_with_till_lookup(sid: str) -> list[Step]:
    return [
        Step("Begin",  PATH_FOR["BeginTransactionWithTillLookup"], begin_payload(sid), 200),
        Step("Sale (with till lookup)", PATH_FOR["AddTransactionSaleLineWithTillLookup"],
             {**sale_payload(sid, 1, lineTimeStamp=datetime.now(timezone.utc).isoformat()),
              "event": "AddTransactionSaleLineWithTillLookup"}, 200),
        Step("Commit", PATH_FOR["CommitTransaction"], commit_payload(sid, "VERIFY-BILL-WTL"), 200),
    ]


def _wrong_auth(sid: str) -> list[Step]:
    return [
        Step("Begin with bad auth", PATH_FOR["BeginTransactionWithTillLookup"],
             begin_payload(sid), 401, auth_key="definitely-not-the-key"),
    ]


def _missing_auth(sid: str) -> list[Step]:
    # NB: only fails if the backend has push_auth_key set (it's "test" by default).
    return [
        Step("Begin with no auth header", PATH_FOR["BeginTransactionWithTillLookup"],
             begin_payload(sid), 401, auth_key=""),
    ]


def _stringified_body(sid: str) -> list[Step]:
    payload = begin_payload(sid)
    stringified = json.dumps(json.dumps(payload))   # double-encode → server unwraps
    return [
        Step("Begin (stringified JSON body)",
             PATH_FOR["BeginTransactionWithTillLookup"],
             stringified, 200),
    ]


def _event_path_mismatch(sid: str) -> list[Step]:
    # Post a Commit-shaped body to the Begin route.
    return [
        Step("Commit body posted to Begin route → 400 mismatch",
             PATH_FOR["BeginTransactionWithTillLookup"],
             commit_payload(sid), 400, expect_message_contains="event mismatch"),
    ]


def _commit_without_begin(sid: str) -> list[Step]:
    # No prior Begin → receiver acks with 200 and a distinguishing message so
    # Nukkad's queue doesn't keep retrying. Lenient by design: a Commit landing
    # without a session is usually a stray retry or a post-restart replay, not
    # a fraud signal. The "no session matched" message lets us tell it apart
    # from a real commit in logs.
    return [
        Step("Commit for unknown session → 200 'no session matched'",
             PATH_FOR["CommitTransaction"],
             commit_payload(sid, "VERIFY-BILL-ORPHAN"), 200,
             expect_message_contains="no session matched"),
    ]


def _sale_before_begin(sid: str) -> list[Step]:
    # Spec is silent; current assembler buffers/fails. Either 200 or 400 acceptable —
    # we just want to confirm the server doesn't crash (no 500).
    return [
        Step("Sale line for unknown session — must not 500",
             PATH_FOR["AddTransactionSaleLine"],
             sale_payload(sid, 1), 0),  # 0 = any 2xx/4xx is fine, fail only on 5xx/network
    ]


def _duplicate_event(sid: str) -> list[Step]:
    # Same Begin twice → second hit should be flagged duplicate.
    payload = begin_payload(sid)
    return [
        Step("Begin (first)",   PATH_FOR["BeginTransactionWithTillLookup"], payload, 200),
        Step("Begin (duplicate) → 'duplicate, ignored'",
             PATH_FOR["BeginTransactionWithTillLookup"], payload, 200,
             expect_message_contains="duplicate"),
    ]


def _empty_transaction(sid: str) -> list[Step]:
    # Begin then immediately Commit, no sale lines. Should still 200 (null txn).
    return [
        Step("Begin",                          PATH_FOR["BeginTransactionWithTillLookup"], begin_payload(sid), 200),
        Step("Commit (no sale lines, no total)", PATH_FOR["CommitTransaction"], commit_payload(sid, "VERIFY-BILL-EMPTY"), 200),
    ]


def _suspend_resume(sid: str) -> list[Step]:
    return [
        Step("Begin",     PATH_FOR["BeginTransactionWithTillLookup"], begin_payload(sid), 200),
        Step("Sale",      PATH_FOR["AddTransactionSaleLine"],         sale_payload(sid, 1), 200),
        Step("Suspended", PATH_FOR["AddTransactionEvent"],            add_event_payload(sid, "TransactionSuspended"), 200),
        Step("Resumed",   PATH_FOR["AddTransactionEvent"],            add_event_payload(sid, "TransactionResumed"), 200),
        Step("Commit",    PATH_FOR["CommitTransaction"],              commit_payload(sid, "VERIFY-BILL-SUSPEND"), 200),
    ]


def _split_tender(sid: str) -> list[Step]:
    return [
        Step("Begin",       PATH_FOR["BeginTransactionWithTillLookup"], begin_payload(sid), 200),
        Step("Sale 200",    PATH_FOR["AddTransactionSaleLine"],         sale_payload(sid, 1, totalAmount=200.0), 200),
        Step("Pay 100 cash",PATH_FOR["AddTransactionPaymentLine"],      payment_payload(sid, amount=100.0,
                                                                                lineNumber=1, lineAttribute="Cash",
                                                                                paymentDescription="Cash"), 200),
        Step("Pay 100 UPI", PATH_FOR["AddTransactionPaymentLine"],      payment_payload(sid, amount=100.0,
                                                                                lineNumber=2, lineAttribute="UPI",
                                                                                paymentDescription="UPI"), 200),
        Step("Total 200",   PATH_FOR["AddTransactionTotalLine"],        total_payload(sid, amount=200.0), 200),
        Step("Commit",      PATH_FOR["CommitTransaction"],              commit_payload(sid, "VERIFY-BILL-SPLIT"), 200),
    ]


def _get_till_assigns(sid: str) -> list[Step]:
    # GetTill is a till-assignment RPC: Nukkad's POS gates the entire
    # transaction flow on a successful response (Begin/Sale/Commit don't fire
    # until we hand back a Till). So GetTill MUST always succeed, including
    # for out-of-scope stores. The Till value Nukkad reuses downstream is
    # numeric — we derive it deterministically from posTerminalNo digits.
    return [
        Step("GetTill (POS 1) → numeric Till",
             PATH_FOR["GetTill"], get_till_payload(), 200,
             expect_message_contains="Success",
             expect_data_keys=("ErrorCode", "Succeeded", "Till")),
    ]


def _bill_reprint(sid: str) -> list[Step]:
    # Spec §4.9: billNumber + transactionTimestamp (Long ms).
    return [
        Step("BillReprint (billNumber + Long ms)",
             PATH_FOR["BillReprint"],
             bill_reprint_payload(f"VERIFY-BILL-RP-{sid[-6:]}"), 200),
    ]


def _malformed_json(sid: str) -> list[Step]:
    return [
        Step("Garbage body → 400",
             PATH_FOR["BeginTransactionWithTillLookup"],
             "this is not json", 400),
    ]


SCENARIOS: list[Scenario] = [
    Scenario("happy_path",        "Happy path — full transaction",       "Begin → 2 sale lines → payment → total → commit (asserts Begin envelope shape)", _happy_path),
    Scenario("with_till_lookup",  "AddTransactionSaleLineWithTillLookup","Begin → sale-with-till-lookup → commit", _sale_line_with_till_lookup),
    Scenario("split_tender",      "Split tender (cash + UPI)",           "Two payment lines on the same session", _split_tender),
    Scenario("suspend_resume",    "Suspend → Resume → Commit",           "AddTransactionEvent lifecycle", _suspend_resume),
    Scenario("empty_transaction", "Empty transaction",                   "Begin then Commit with no sale lines", _empty_transaction),
    Scenario("fractional_qty",    "Fractional itemQuantity (1.250 kg)",  "Real grocery POS sends decimal kg quantities", _fractional_qty),
    Scenario("stringified_bool",  "Stringified bool flags",              "employeePurchase=\"false\" must not flip to True", _stringified_bool),
    Scenario("get_till_assigns",  "GetTill assigns a Till",              "Must always succeed — Nukkad's POS gates the whole transaction flow on this", _get_till_assigns),
    Scenario("bill_reprint",      "BillReprint standalone",              "Reprint event (spec keys: billNumber + Long-ms transactionTimestamp)", _bill_reprint),
    Scenario("duplicate_event",   "Duplicate-event dedupe",              "Same Begin twice — second is dropped as duplicate", _duplicate_event),
    Scenario("commit_no_begin",   "Commit without prior Begin",          "Should 400, not 500", _commit_without_begin),
    Scenario("sale_no_begin",     "Sale line for unknown session",       "Should not 500", _sale_before_begin),
    Scenario("event_mismatch",    "Event/route mismatch",                "Commit body posted to Begin route → 400", _event_path_mismatch),
    Scenario("malformed_json",    "Malformed JSON body",                 "Garbage body → 400", _malformed_json),
    Scenario("stringified_body",  "Stringified-JSON body accepted",      "Double-encoded body still parses", _stringified_body),
    Scenario("wrong_auth",        "Wrong auth key → 401",                "Bogus x-authorization-key value", _wrong_auth),
    Scenario("missing_auth",      "Missing auth header → 401",           "No x-authorization-key sent (only fails if backend has key set)", _missing_auth),
]


# ---------- runner -----------------------------------------------------------

def pad(s: str, n: int) -> str:
    s = str(s)
    return s + " " * (n - len(s)) if len(s) < n else s[: n - 1] + "…"


def run_scenario(base: str, default_auth: str, scn: Scenario, timeout: float) -> tuple[bool, list[tuple[Step, int, str, str]]]:
    sid = f"verify-{scn.key}-{uuid.uuid4().hex[:8]}"
    rows: list[tuple[Step, int, str, str]] = []   # (step, status, why, raw)
    overall_ok = True

    for step in scn.steps_factory(sid):
        url = f"{base}{step.path}"
        if isinstance(step.payload, str):
            body = step.payload.encode("utf-8")
        else:
            body = json.dumps(step.payload).encode("utf-8")

        headers: dict[str, str] = {}
        if step.auth_key is None:
            headers["x-authorization-key"] = default_auth
        elif step.auth_key == "":
            pass  # explicitly omit
        else:
            headers["x-authorization-key"] = step.auth_key

        status, raw, parsed = post(url, headers, body, timeout=timeout)

        why_parts: list[str] = []
        if step.expected_status == 0:
            ok = 200 <= status < 500 and status != 0
            if not ok:
                why_parts.append(f"expected non-5xx, got {status}")
        else:
            ok = status == step.expected_status
            if not ok:
                why_parts.append(f"expected {step.expected_status}, got {status}")

        if step.expect_message_contains:
            msg = (parsed or {}).get("message", "") if isinstance(parsed, dict) else ""
            if step.expect_message_contains.lower() not in str(msg).lower():
                ok = False
                why_parts.append(f"message does not contain '{step.expect_message_contains}': {msg!r}")

        if step.expect_data_keys:
            data = (parsed or {}).get("data") if isinstance(parsed, dict) else None
            data_keys = set(data.keys()) if isinstance(data, dict) else set()
            missing = [k for k in step.expect_data_keys if k not in data_keys]
            if missing:
                ok = False
                why_parts.append(f"response.data missing keys: {missing}")

        rows.append((step, status, "; ".join(why_parts), raw))
        if not ok:
            overall_ok = False

    return overall_ok, rows


def print_scenario(scn: Scenario, ok: bool, rows: list[tuple[Step, int, str, str]]) -> None:
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {scn.name}")
    for step, status, why, raw in rows:
        marker = "  ok " if not why else "  !! "
        print(f"  {marker}{pad(step.label, 44)}  {status:>3}  {step.path}")
        if why:
            print(f"        why     : {why}")
            snippet = raw.replace("\n", " ").strip()
            if snippet:
                print(f"        body    : {snippet[:160]}")


# ---------- main -------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the 9 RLCC push endpoints with scenario-based smoke tests")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8001"),
                        help="Backend base URL (default: $BASE_URL or http://localhost:8001)")

    env_key = os.getenv("NUKKAD_PUSH_AUTH_KEY", "")
    dotenv_key, dotenv_source = ("", "")
    if not env_key:
        dotenv_key, dotenv_source = _load_dotenv_auth_key()

    parser.add_argument("--auth-key", default=env_key or dotenv_key or "test",
                        help="x-authorization-key value (default: $NUKKAD_PUSH_AUTH_KEY, then poc/.env, then 'test')")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds (default 10)")
    parser.add_argument("--only", default="",
                        help="Comma-separated scenario keys to run (see --list)")
    parser.add_argument("--list", action="store_true", help="List scenarios and exit")
    args = parser.parse_args()

    if args.list:
        print(f"\n{len(SCENARIOS)} scenarios:\n")
        for scn in SCENARIOS:
            print(f"  {pad(scn.key, 22)} {scn.name}")
            print(f"  {' ' * 22} {scn.description}")
        return 0

    base = args.base_url.rstrip("/")
    selected = SCENARIOS
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        selected = [scn for scn in SCENARIOS if scn.key in wanted]
        unknown = wanted - {scn.key for scn in SCENARIOS}
        if unknown:
            print(f"unknown scenario keys: {sorted(unknown)}", file=sys.stderr)
            return 2

    auth_source = (
        "$NUKKAD_PUSH_AUTH_KEY" if env_key
        else dotenv_source if dotenv_key
        else "fallback 'test'"
    )

    print(f"\nRLCC push endpoint verifier")
    print(f"  base_url   : {base}")
    print(f"  auth_key   : {'(set)' if args.auth_key else '(empty)'}  [from {auth_source}]")
    print(f"  scenarios  : {len(selected)}")
    print(f"  ts         : {datetime.now().isoformat(timespec='seconds')}\n")

    started = time.monotonic()
    fails = 0
    for scn in selected:
        ok, rows = run_scenario(base, args.auth_key, scn, args.timeout)
        print_scenario(scn, ok, rows)
        if not ok:
            fails += 1
        print()

    elapsed = int((time.monotonic() - started) * 1000)
    print("-" * 78)
    print(f"  passed: {len(selected) - fails} / {len(selected)}   failed: {fails}   elapsed: {elapsed}ms\n")
    return 0 if fails == 0 else fails


if __name__ == "__main__":
    sys.exit(main())
