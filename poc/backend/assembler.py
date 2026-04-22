from __future__ import annotations

import time

from backend.config import build_seller_window_id, normalize_terminal
from backend.models import PaymentLine, SaleLine, TotalLine, TransactionEvent, TransactionSession, utc_now


class TransactionAssembler:
    def __init__(self, timeout_seconds: int = 1800):
        self.sessions: dict[str, TransactionSession] = {}
        self.timeout = timeout_seconds
        self._buffer: list[tuple[str, dict]] = []
        self._session_times: dict[str, float] = {}

    def begin(self, payload: dict):
        session_id = payload["transactionSessionId"]
        store_id = payload.get("storeIdentifier", "")
        pos_terminal_no = payload.get("posTerminalNo", "")
        txn = TransactionSession(
            id=session_id,
            store_id=store_id,
            pos_terminal_no=pos_terminal_no,
            display_pos_label=pos_terminal_no,
            seller_window_id=build_seller_window_id(store_id, pos_terminal_no),
            cashier_id=payload.get("cashier", ""),
            debitor=payload.get("debitor", ""),
            transaction_type=payload.get("transactionType", "CompletedNormally"),
            employee_purchase=bool(payload.get("employeePurchase", False)),
            outside_opening_hours=payload.get("outsideOpeningHours", "InsideOpeningHours"),
            started_at=payload.get("transactionTimeStamp"),
            last_event_at=payload.get("transactionTimeStamp"),
            source="push_assembled",
            status="open",
            is_previous_transaction=bool(payload.get("isPreviousTransaction", False)),
            linked_transaction_id=payload.get("transactionNumberLinkedTo", ""),
            transaction_number=payload.get("transactionNumber", ""),
        )
        self.sessions[session_id] = txn
        self._session_times[session_id] = time.monotonic()
        self._flush_buffer(session_id)

    def add_sale_line(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.items.append(SaleLine.from_nukkad(payload))
            session.last_event_at = payload.get("lineTimeStamp") or session.last_event_at
        else:
            self._buffer.append(("sale_line", payload))

    def add_payment_line(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.payments.append(PaymentLine.from_nukkad(payload))
            session.last_event_at = payload.get("lineTimeStamp") or session.last_event_at
        else:
            self._buffer.append(("payment_line", payload))

    def add_total_line(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.totals.append(TotalLine.from_nukkad(payload))
            session.last_event_at = payload.get("lineTimeStamp") or session.last_event_at
        else:
            self._buffer.append(("total_line", payload))

    def add_event(self, payload: dict):
        session = self._get_session(payload)
        if session:
            event = TransactionEvent.from_nukkad(payload)
            session.events.append(event)
            session.last_event_at = event.line_timestamp or session.last_event_at
            if event.line_attribute == "DrawerOpenedOutsideATransaction":
                session.transaction_type = "DrawerOpenedOutsideATransaction"
        else:
            self._buffer.append(("event", payload))

    def commit(self, payload: dict) -> TransactionSession | None:
        session_id = payload.get("transactionSessionId", "")
        session = self.sessions.pop(session_id, None)
        self._session_times.pop(session_id, None)
        if session:
            session.bill_number = payload.get("transactionNumber", "") or session.bill_number
            session.transaction_number = payload.get("transactionNumber", "") or session.transaction_number
            session.status = "committed"
            session.committed_at = utc_now()
            session.last_event_at = payload.get("transactionTimeStamp") or session.last_event_at
            return session
        return None

    def check_expired(self) -> list[TransactionSession]:
        now = time.monotonic()
        expired: list[TransactionSession] = []
        expired_ids: list[str] = []
        for session_id, created in self._session_times.items():
            if now - created >= self.timeout:
                expired_ids.append(session_id)
        for session_id in expired_ids:
            session = self.sessions.pop(session_id, None)
            self._session_times.pop(session_id, None)
            if session:
                session.status = "expired"
                expired.append(session)
        return expired

    def has_open_session(self, store_id: str, pos_terminal_no: str) -> bool:
        normalized = normalize_terminal(pos_terminal_no)
        return any(
            session.store_id == store_id and normalize_terminal(session.pos_terminal_no) == normalized
            for session in self.sessions.values()
        )

    def _get_session(self, payload: dict) -> TransactionSession | None:
        return self.sessions.get(payload.get("transactionSessionId", ""))

    def _flush_buffer(self, session_id: str):
        remaining = []
        for event_type, payload in self._buffer:
            if payload.get("transactionSessionId") != session_id:
                remaining.append((event_type, payload))
                continue

            session = self.sessions[session_id]
            if event_type == "sale_line":
                session.items.append(SaleLine.from_nukkad(payload))
            elif event_type == "payment_line":
                session.payments.append(PaymentLine.from_nukkad(payload))
            elif event_type == "total_line":
                session.totals.append(TotalLine.from_nukkad(payload))
            elif event_type == "event":
                session.events.append(TransactionEvent.from_nukkad(payload))

        self._buffer = remaining
