from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.models import Alert, TransactionSession


# Risk weights for escalation
_HIGH = "High"
_MEDIUM = "Medium"
_LOW = "Low"

# Rule severity map (rules not listed default to Medium)
_RULE_SEVERITY: dict[str, str] = {
    "5_negative_amount": _HIGH,
    "12_drawer_opened": _HIGH,
    "15_post_bill_cancel": _HIGH,
    "23_full_return": _HIGH,
    "26_void_no_customer": _HIGH,
    "27_return_no_customer": _HIGH,
    "28_drawer_no_customer": _HIGH,
    "18_employee_purchase": _LOW,
}


_TOTAL_ALIASES: dict[str, tuple[str, ...]] = {
    "TotalAmountToBePaid": ("TotalAmountToBePaid", "GrandTotal", "Total"),
}


def _get_total(txn: TransactionSession, attribute: str) -> Optional[float]:
    aliases = _TOTAL_ALIASES.get(attribute, (attribute,))
    for t in txn.totals:
        if t.line_attribute in aliases:
            return t.amount
    return None


class FraudEngine:
    def __init__(self, config: dict) -> None:
        self._config = config
        self._rules_cfg: dict = config.get("rules", {})
        # thresholds
        self._discount_pct: float = float(config.get("discount_threshold_percent", 20))
        self._refund_threshold: float = float(config.get("refund_amount_threshold", 0))
        self._high_value: float = float(config.get("high_value_threshold", 2000))
        self._bulk_qty: int = int(config.get("bulk_quantity_threshold", 10))
        self._void_pct: float = float(config.get("void_percentage_threshold", 50))
        self._feed_down_minutes: int = int(config.get("feed_down_minutes", 10))
        # feed-down tracking: store_id -> last event datetime
        self._last_event: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Feed-down helpers
    # ------------------------------------------------------------------

    def record_nukkad_event(self, store_id: str) -> None:
        """Record that a Nukkad event was received for a store."""
        self._last_event[store_id] = datetime.now(timezone.utc)

    def is_feed_down(self, store_id: str) -> bool:
        """Return True if no events received for >feed_down_minutes."""
        last = self._last_event.get(store_id)
        if last is None:
            return False
        delta = datetime.now(timezone.utc) - last
        return delta > timedelta(minutes=self._feed_down_minutes)

    # ------------------------------------------------------------------
    # Rule evaluation helpers
    # ------------------------------------------------------------------

    def _enabled(self, rule_id: str) -> bool:
        return self._rules_cfg.get(rule_id, {}).get("enabled", True)

    def _trigger(self, txn: TransactionSession, rule_id: str) -> None:
        if rule_id not in txn.triggered_rules:
            txn.triggered_rules.append(rule_id)

    # ------------------------------------------------------------------
    # Individual rules
    # ------------------------------------------------------------------

    def _rule_1_high_discount(self, txn: TransactionSession) -> None:
        """High discount: any item discount% > threshold (excludes manually-entered discounts covered by rule 10)."""
        _manual_types = ("ManuallyEnteredValue", "ManuallyEnteredPercentage")
        for item in txn.items:
            if item.discount_type in _manual_types:
                continue  # rule 10 handles these
            if item.discount > 0 and item.total_amount > 0:
                original = item.total_amount + item.discount
                pct = (item.discount / original) * 100
                if pct > self._discount_pct:
                    self._trigger(txn, "1_high_discount")
                    return
            elif item.discount > self._discount_pct:
                # discount stored as percent
                self._trigger(txn, "1_high_discount")
                return

    def _rule_2_refund_excess(self, txn: TransactionSession) -> None:
        """Refund/excess cash: ReturnCash payment > threshold."""
        for p in txn.payments:
            if p.line_attribute == "ReturnCash" and p.amount > self._refund_threshold:
                self._trigger(txn, "2_refund_excess")
                return

    def _rule_3_complementary(self, txn: TransactionSession) -> None:
        if txn.transaction_type == "Complementary":
            self._trigger(txn, "3_complementary")

    def _rule_4_void_cancelled(self, txn: TransactionSession) -> None:
        if txn.transaction_type in ("Cancelled", "Suspended"):
            self._trigger(txn, "4_void_cancelled")
            return
        for ev in txn.events:
            if ev.line_attribute == "TransactionCancelled":
                self._trigger(txn, "4_void_cancelled")
                return

    def _rule_5_negative_amount(self, txn: TransactionSession) -> None:
        amount = _get_total(txn, "TotalAmountToBePaid")
        if amount is not None and amount < 0:
            self._trigger(txn, "5_negative_amount")

    def _rule_6_high_value(self, txn: TransactionSession) -> None:
        amount = _get_total(txn, "TotalAmountToBePaid")
        if amount is not None and amount > self._high_value:
            self._trigger(txn, "6_high_value")

    def _rule_7_bulk_purchase(self, txn: TransactionSession) -> None:
        total_qty = sum(item.item_quantity for item in txn.items)
        if total_qty > self._bulk_qty:
            self._trigger(txn, "7_bulk_purchase")

    def _rule_8_manual_entry(self, txn: TransactionSession) -> None:
        for item in txn.items:
            if item.scan_attribute == "ManuallyEntered":
                self._trigger(txn, "8_manual_entry")
                return

    def _rule_9_manual_price(self, txn: TransactionSession) -> None:
        for item in txn.items:
            if item.scan_attribute == "ModifiedUnitPrice":
                self._trigger(txn, "9_manual_price")
                return

    def _rule_10_manual_discount(self, txn: TransactionSession) -> None:
        for item in txn.items:
            if item.discount_type in ("ManuallyEnteredValue", "ManuallyEnteredPercentage"):
                self._trigger(txn, "10_manual_discount")
                return

    def _rule_11_self_granted_discount(self, txn: TransactionSession) -> None:
        for item in txn.items:
            if item.granted_by and item.granted_by == txn.cashier_id and item.discount > 0:
                self._trigger(txn, "11_self_granted_discount")
                return

    def _rule_12_drawer_opened(self, txn: TransactionSession) -> None:
        if txn.transaction_type == "DrawerOpenedOutsideATransaction":
            self._trigger(txn, "12_drawer_opened")

    # Rule 13 (bill reprint) is handled in receiver, not here.

    def _rule_14_null_transaction(self, txn: TransactionSession) -> None:
        """Committed transaction with 0 items."""
        if txn.status == "committed" and len(txn.items) == 0:
            self._trigger(txn, "14_null_transaction")

    def _rule_15_post_bill_cancel(self, txn: TransactionSession) -> None:
        if txn.transaction_type == "CancellationOfPrevious":
            self._trigger(txn, "15_post_bill_cancel")

    def _rule_16_return_not_recent(self, txn: TransactionSession) -> None:
        for item in txn.items:
            if item.item_attribute == "ReturnNotRecentlySold":
                self._trigger(txn, "16_return_not_recent")
                return

    def _rule_17_exchange_no_match(self, txn: TransactionSession) -> None:
        for item in txn.items:
            if item.item_attribute == "ExchangeSlipWithoutMatchingLine":
                self._trigger(txn, "17_exchange_no_match")
                return

    def _rule_18_employee_purchase(self, txn: TransactionSession) -> None:
        if txn.employee_purchase:
            self._trigger(txn, "18_employee_purchase")

    def _rule_19_void_percentage(self, txn: TransactionSession) -> None:
        total = len(txn.items)
        if total == 0:
            return
        void_count = sum(1 for i in txn.items if i.item_attribute == "CancellationWithinTransaction")
        pct = (void_count / total) * 100
        if pct > self._void_pct:
            self._trigger(txn, "19_void_percentage")

    def _rule_20_outside_hours(self, txn: TransactionSession) -> None:
        if txn.outside_opening_hours != "InsideOpeningHours":
            self._trigger(txn, "20_outside_hours")

    def _rule_21_credit_note(self, txn: TransactionSession) -> None:
        for p in txn.payments:
            if p.line_attribute == "CreditNotePayment":
                self._trigger(txn, "21_credit_note")
                return

    # Rule 22 (manual card) skipped — speculative.

    def _rule_23_full_return(self, txn: TransactionSession) -> None:
        if not txn.items:
            return
        if all(i.item_attribute == "ReturnItem" for i in txn.items):
            self._trigger(txn, "23_full_return")

    # Rules 24-25 are CV-only, handled by cv_consumer.

    def _rule_26_void_no_customer(self, txn: TransactionSession) -> None:
        has_void = any(i.item_attribute == "CancellationWithinTransaction" for i in txn.items)
        if has_void and txn.cv_non_seller_present is False:
            self._trigger(txn, "26_void_no_customer")

    def _rule_27_return_no_customer(self, txn: TransactionSession) -> None:
        has_return = any(i.item_attribute == "ReturnItem" for i in txn.items)
        if has_return and txn.cv_non_seller_present is False:
            self._trigger(txn, "27_return_no_customer")

    def _rule_28_drawer_no_customer(self, txn: TransactionSession) -> None:
        if txn.transaction_type == "DrawerOpenedOutsideATransaction" and txn.cv_non_seller_present is False:
            self._trigger(txn, "28_drawer_no_customer")

    def _rule_29_bill_not_generated(self, txn: TransactionSession) -> None:
        if txn.status == "committed" and txn.cv_receipt_detected is False:
            self._trigger(txn, "29_bill_not_generated")

    # ------------------------------------------------------------------
    # Main evaluate method
    # ------------------------------------------------------------------

    _ALL_RULES = [
        ("1_high_discount", "_rule_1_high_discount"),
        ("2_refund_excess", "_rule_2_refund_excess"),
        ("3_complementary", "_rule_3_complementary"),
        ("4_void_cancelled", "_rule_4_void_cancelled"),
        ("5_negative_amount", "_rule_5_negative_amount"),
        ("6_high_value", "_rule_6_high_value"),
        ("7_bulk_purchase", "_rule_7_bulk_purchase"),
        ("8_manual_entry", "_rule_8_manual_entry"),
        ("9_manual_price", "_rule_9_manual_price"),
        ("10_manual_discount", "_rule_10_manual_discount"),
        ("11_self_granted_discount", "_rule_11_self_granted_discount"),
        ("12_drawer_opened", "_rule_12_drawer_opened"),
        ("14_null_transaction", "_rule_14_null_transaction"),
        ("15_post_bill_cancel", "_rule_15_post_bill_cancel"),
        ("16_return_not_recent", "_rule_16_return_not_recent"),
        ("17_exchange_no_match", "_rule_17_exchange_no_match"),
        ("18_employee_purchase", "_rule_18_employee_purchase"),
        ("19_void_percentage", "_rule_19_void_percentage"),
        ("20_outside_hours", "_rule_20_outside_hours"),
        ("21_credit_note", "_rule_21_credit_note"),
        ("23_full_return", "_rule_23_full_return"),
        ("26_void_no_customer", "_rule_26_void_no_customer"),
        ("27_return_no_customer", "_rule_27_return_no_customer"),
        ("28_drawer_no_customer", "_rule_28_drawer_no_customer"),
        ("29_bill_not_generated", "_rule_29_bill_not_generated"),
    ]

    def evaluate(self, txn: TransactionSession) -> list[Alert]:
        # Reset state
        txn.triggered_rules = []
        txn.risk_level = _LOW

        # Run each enabled rule
        for rule_id, method_name in self._ALL_RULES:
            if self._enabled(rule_id):
                getattr(self, method_name)(txn)

        # Compute risk level from triggered rules
        txn.risk_level = self._compute_risk(txn.triggered_rules)

        # Produce alerts for High/Medium
        alerts: list[Alert] = []
        if txn.risk_level in (_HIGH, _MEDIUM):
            alert = Alert(
                transaction_id=txn.id,
                store_id=txn.store_id,
                store_name=txn.store_name,
                pos_terminal_no=txn.pos_terminal_no,
                display_pos_label=txn.display_pos_label,
                pos_zone=txn.pos_terminal,
                cashier_id=txn.cashier_id,
                risk_level=txn.risk_level,
                triggered_rules=list(txn.triggered_rules),
                camera_id=txn.camera_id,
                device_id=txn.device_id,
                snippet_path=txn.snippet_path,
                source="rule",
            )
            alerts.append(alert)

        return alerts

    def _compute_risk(self, triggered_rules: list[str]) -> str:
        """Escalate risk: any HIGH → HIGH, 3+ MEDIUM → HIGH, 1-2 MEDIUM → MEDIUM, else LOW."""
        has_high = False
        medium_count = 0
        has_any = False

        for rule_id in triggered_rules:
            severity = _RULE_SEVERITY.get(rule_id, _MEDIUM)
            if severity == _HIGH:
                has_high = True
            elif severity == _MEDIUM:
                medium_count += 1
            has_any = True

        if has_high:
            return _HIGH
        if medium_count >= 3:
            return _HIGH
        if medium_count >= 1:
            return _MEDIUM
        if has_any:
            # Only LOW rules triggered
            return _LOW
        return _LOW
