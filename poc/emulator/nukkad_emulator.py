"""Nukkad POS event emulator — generates realistic transaction events for backend testing."""

import argparse
import json
import random
import time
import uuid
from datetime import datetime, timezone

import httpx

from emulator.scenarios import (
    CASHIERS,
    STORES,
    pick_cashier,
    pick_items,
    pick_pay_mode,
    scenario_high_discount,
    scenario_manual_discount,
    scenario_manual_entry,
    scenario_return_not_recent,
    scenario_void_item,
)

FRAUD_SCENARIOS = [
    "manual_entry",
    "manual_discount",
    "high_discount",
    "void_item",
    "return_not_recent",
    "null_transaction",
    "drawer_opened",
    "reprint",
    "employee_purchase",
]


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def gen_session_id() -> str:
    return str(uuid.uuid4())


def gen_bill_number() -> str:
    return f"BILL-{random.randint(10000, 99999)}"


def send_event(client: httpx.Client, url: str, payload: dict) -> bool:
    """Send a single event as stringified JSON (double-encoded) to match Nukkad format."""
    body = json.dumps(json.dumps(payload))
    try:
        resp = client.post(
            f"{url}/v1/rlcc/launch-event",
            content=body,
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        return resp.status_code == 200
    except httpx.RequestError as exc:
        print(f"  [ERROR] Could not reach backend: {exc}")
        return False


def build_begin(session_id: str, store: dict, cashier: str, employee_purchase: bool = False) -> dict:
    return {
        "event": "BeginTransactionWithTillLookup",
        "transactionSessionId": session_id,
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "cashier": cashier,
        "transactionType": "CompletedNormally",
        "employeePurchase": employee_purchase,
        "outsideOpeningHours": "InsideOpeningHours",
        "transactionTimeStamp": now_ts(),
    }


def build_sale_line(session_id: str, store: dict, cashier: str, item: dict, line_number: int) -> dict:
    qty = item.get("qty", 1)
    unit_price = item["price"]
    total = round(unit_price * qty - item.get("discount", 0.0), 2)
    return {
        "event": "AddTransactionSaleLine",
        "transactionSessionId": session_id,
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "cashier": cashier,
        "lineTimeStamp": now_ts(),
        "lineNumber": line_number,
        "itemID": item["code"],
        "itemDescription": item["name"],
        "itemQuantity": qty,
        "itemUnitPrice": unit_price,
        "totalAmount": total,
        "scanAttribute": item.get("scan_attribute", "None"),
        "itemAttribute": item.get("item_attribute", "None"),
        "discountType": item.get("discount_type", "NoLineDiscount"),
        "discount": item.get("discount", 0.0),
        "grantedBy": item.get("granted_by", ""),
    }


def build_payment_line(session_id: str, store: dict, cashier: str, amount: float, pay_mode: str, line_number: int) -> dict:
    return {
        "event": "AddTransactionPaymentLine",
        "transactionSessionId": session_id,
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "cashier": cashier,
        "lineTimeStamp": now_ts(),
        "lineNumber": line_number,
        "lineAttribute": "None",
        "paymentDescription": pay_mode,
        "amount": amount,
        "cardType": "",
        "paymentTypeID": pay_mode.upper(),
    }


def build_total_line(session_id: str, store: dict, cashier: str, amount: float) -> dict:
    return {
        "event": "AddTransactionTotalLine",
        "transactionSessionId": session_id,
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "cashier": cashier,
        "lineAttribute": "GrandTotal",
        "totalDescription": "Grand Total",
        "amount": amount,
    }


def build_commit(session_id: str, store: dict, cashier: str) -> dict:
    return {
        "event": "CommitTransaction",
        "transactionSessionId": session_id,
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "cashier": cashier,
        "transactionNumber": gen_bill_number(),
        "transactionTimeStamp": now_ts(),
    }


def build_drawer_event(store: dict, cashier: str) -> dict:
    return {
        "event": "AddTransactionEvent",
        "transactionSessionId": gen_session_id(),
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "cashier": cashier,
        "lineTimeStamp": now_ts(),
        "lineAttribute": "DrawerOpenedOutsideATransaction",
        "eventDescription": "Cash drawer opened outside of a transaction",
    }


def build_reprint_event(store: dict, cashier: str) -> dict:
    return {
        "event": "BillReprint",
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "cashier": cashier,
        "transactionTimeStamp": now_ts(),
        "transactionNumber": gen_bill_number(),
    }


def run_normal_transaction(client: httpx.Client, url: str, store: dict, cashier: str,
                           items: list, fraud_scenario: str | None) -> tuple[int, float]:
    """Send a full transaction sequence. Returns (event_count, total_amount)."""
    session_id = gen_session_id()
    employee_purchase = (fraud_scenario == "employee_purchase")

    # Apply fraud scenario mutations to items
    if fraud_scenario == "manual_entry":
        items = scenario_manual_entry(items)
    elif fraud_scenario == "manual_discount":
        items = scenario_manual_discount(items, cashier)
    elif fraud_scenario == "high_discount":
        items = scenario_high_discount(items)
    elif fraud_scenario == "void_item":
        items = scenario_void_item(items)
    elif fraud_scenario == "return_not_recent":
        items = scenario_return_not_recent(items)

    events_sent = 0

    # BeginTransaction
    send_event(client, url, build_begin(session_id, store, cashier, employee_purchase=employee_purchase))
    events_sent += 1

    # SaleLines
    total_amount = 0.0
    for i, item in enumerate(items, start=1):
        qty = item.get("qty", 1)
        total_amount += round(item["price"] * qty - item.get("discount", 0.0), 2)
        send_event(client, url, build_sale_line(session_id, store, cashier, item, i))
        events_sent += 1

    total_amount = round(total_amount, 2)
    pay_mode = pick_pay_mode()

    # PaymentLine
    send_event(client, url, build_payment_line(session_id, store, cashier, total_amount, pay_mode, len(items) + 1))
    events_sent += 1

    # TotalLine
    send_event(client, url, build_total_line(session_id, store, cashier, total_amount))
    events_sent += 1

    # CommitTransaction
    send_event(client, url, build_commit(session_id, store, cashier))
    events_sent += 1

    return events_sent, total_amount


def run_transaction(client: httpx.Client, url: str, fraud_rate: float) -> str:
    """Generate and send one transaction (or standalone event). Returns a log line."""
    store = random.choice(STORES)
    cashier = pick_cashier()

    # Decide if this is a fraud scenario
    fraud_scenario = None
    if random.random() < fraud_rate:
        fraud_scenario = random.choice(FRAUD_SCENARIOS)

    label = f"[FRAUD:{fraud_scenario}]" if fraud_scenario else "[NORMAL]"

    # Handle standalone (non-transaction) fraud events
    if fraud_scenario == "drawer_opened":
        payload = build_drawer_event(store, cashier)
        ok = send_event(client, url, payload)
        status = "OK" if ok else "ERR"
        return f"{now_ts()} {label} DrawerOpened store={store['cin']} cashier={cashier} [{status}]"

    if fraud_scenario == "reprint":
        payload = build_reprint_event(store, cashier)
        ok = send_event(client, url, payload)
        status = "OK" if ok else "ERR"
        return f"{now_ts()} {label} BillReprint store={store['cin']} cashier={cashier} [{status}]"

    if fraud_scenario == "null_transaction":
        # Begin + Commit with no items
        session_id = gen_session_id()
        send_event(client, url, build_begin(session_id, store, cashier))
        send_event(client, url, build_commit(session_id, store, cashier))
        return f"{now_ts()} {label} NullTransaction store={store['cin']} cashier={cashier} [OK]"

    # Full transaction (normal or item-level fraud)
    items = pick_items()
    events_sent, total = run_normal_transaction(client, url, store, cashier, items, fraud_scenario)
    items_count = len(items)
    return (
        f"{now_ts()} {label} txn store={store['cin']} terminal={store['terminal']} "
        f"cashier={cashier} items={items_count} total=₹{total:.2f} events={events_sent}"
    )


def main():
    parser = argparse.ArgumentParser(description="Nukkad POS event emulator")
    parser.add_argument("--url", default="http://localhost:8001", help="Backend base URL")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between transactions")
    parser.add_argument("--fraud-rate", type=float, default=0.15, help="Fraction of transactions with fraud (0-1)")
    args = parser.parse_args()

    fraud_rate = max(0.0, min(1.0, args.fraud_rate))
    print(f"Nukkad emulator starting — url={args.url} interval={args.interval}s fraud_rate={fraud_rate:.0%}")
    print("Press Ctrl+C to stop.\n")

    txn_count = 0
    with httpx.Client() as client:
        while True:
            try:
                log_line = run_transaction(client, args.url, fraud_rate)
                txn_count += 1
                print(f"[{txn_count:04d}] {log_line}")
            except Exception as exc:
                print(f"[{txn_count:04d}] [ERROR] Unexpected error: {exc}")
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
