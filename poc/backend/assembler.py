from backend.models import (
    TransactionSession, SaleLine, PaymentLine, TotalLine, TransactionEvent, utc_now
)
import time


class TransactionAssembler:
    def __init__(self, timeout_seconds: int = 1800):
        self.sessions: dict[str, TransactionSession] = {}
        self.timeout = timeout_seconds
        self._buffer: list[tuple[str, dict]] = []
        self._session_times: dict[str, float] = {}

    def begin(self, payload: dict):
        session_id = payload["transactionSessionId"]
        txn = TransactionSession(
            id=session_id,
            store_id=payload.get("storeIdentifier", ""),
            pos_terminal=payload.get("posTerminalNo", ""),
            cashier_id=payload.get("cashier", ""),
            transaction_type=payload.get("transactionType", "CompletedNormally"),
            employee_purchase=payload.get("employeePurchase", False),
            outside_opening_hours=payload.get("outsideOpeningHours", "InsideOpeningHours"),
            started_at=payload.get("transactionTimeStamp"),
            source="push_assembled",
            status="open",
        )
        self.sessions[session_id] = txn
        self._session_times[session_id] = time.monotonic()
        self._flush_buffer(session_id)

    def add_sale_line(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.items.append(SaleLine.from_nukkad(payload))
        else:
            self._buffer.append(("sale_line", payload))

    def add_payment_line(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.payments.append(PaymentLine.from_nukkad(payload))
        else:
            self._buffer.append(("payment_line", payload))

    def add_total_line(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.totals.append(TotalLine.from_nukkad(payload))
        else:
            self._buffer.append(("total_line", payload))

    def add_event(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.events.append(TransactionEvent.from_nukkad(payload))
        else:
            self._buffer.append(("event", payload))

    def commit(self, payload: dict) -> TransactionSession | None:
        session_id = payload.get("transactionSessionId", "")
        session = self.sessions.pop(session_id, None)
        self._session_times.pop(session_id, None)
        if session:
            session.bill_number = payload.get("transactionNumber", "")
            session.status = "committed"
            session.committed_at = utc_now()
            return session
        return None

    def check_expired(self) -> list[TransactionSession]:
        now = time.monotonic()
        expired = []
        expired_ids = []
        for sid, created in self._session_times.items():
            if now - created > self.timeout:
                expired_ids.append(sid)
        for sid in expired_ids:
            session = self.sessions.pop(sid, None)
            self._session_times.pop(sid, None)
            if session:
                session.status = "expired"
                expired.append(session)
        return expired

    def _get_session(self, payload: dict) -> TransactionSession | None:
        return self.sessions.get(payload.get("transactionSessionId", ""))

    def _flush_buffer(self, session_id: str):
        remaining = []
        for event_type, payload in self._buffer:
            if payload.get("transactionSessionId") == session_id:
                session = self.sessions[session_id]
                if event_type == "sale_line":
                    session.items.append(SaleLine.from_nukkad(payload))
                elif event_type == "payment_line":
                    session.payments.append(PaymentLine.from_nukkad(payload))
                elif event_type == "total_line":
                    session.totals.append(TotalLine.from_nukkad(payload))
                elif event_type == "event":
                    session.events.append(TransactionEvent.from_nukkad(payload))
            else:
                remaining.append((event_type, payload))
        self._buffer = remaining
