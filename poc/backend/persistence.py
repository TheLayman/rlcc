from __future__ import annotations

from datetime import datetime, timedelta, timezone

import backend.deps as deps
from backend.models import Alert, TransactionSession


_IST = timezone(timedelta(hours=5, minutes=30))


def parse_dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_IST).astimezone(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Naive Nukkad timestamps — assume IST per BACKEND_DESIGN §11.
        dt = dt.replace(tzinfo=_IST).astimezone(timezone.utc)
    return dt


def load_transactions() -> list[TransactionSession]:
    txns: list[TransactionSession] = []
    for record in deps.storage.read("transactions"):
        try:
            txns.append(TransactionSession(**record))
        except Exception:
            continue
    return txns


def load_alerts() -> list[Alert]:
    alerts: list[Alert] = []
    for record in deps.storage.read("alerts"):
        try:
            alerts.append(Alert(**record))
        except Exception:
            continue
    return alerts


def save_transactions(transactions: list[TransactionSession]) -> None:
    deps.storage.replace("transactions", [txn.model_dump() for txn in transactions])


def save_alerts(alerts: list[Alert]) -> None:
    deps.storage.replace("alerts", [alert.model_dump() for alert in alerts])


def find_transaction_by_bill_number(bill_number: str) -> TransactionSession | None:
    if not bill_number:
        return None
    for txn in load_transactions():
        if txn.bill_number == bill_number:
            return txn
    return None


def sort_transactions(transactions: list[TransactionSession]) -> list[TransactionSession]:
    return sorted(
        transactions,
        key=lambda txn: parse_dt(txn.committed_at or txn.started_at or txn.last_event_at)
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def sort_alerts(alerts: list[Alert]) -> list[Alert]:
    return sorted(alerts, key=lambda alert: parse_dt(alert.timestamp) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
