from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def gen_id(prefix: str = "ALT") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


class SaleLine(BaseModel):
    line_timestamp: Optional[str] = None
    line_number: int = 0
    item_id: str = ""
    item_description: str = ""
    item_quantity: float = 0.0
    item_unit_price: float = 0.0
    total_amount: float = 0.0
    scan_attribute: str = "None"
    item_attribute: str = "None"
    discount_type: str = "NoLineDiscount"
    discount: float = 0.0
    granted_by: str = ""

    @classmethod
    def from_nukkad(cls, payload: dict) -> SaleLine:
        return cls(
            line_timestamp=payload.get("lineTimeStamp"),
            line_number=payload.get("lineNumber", 0),
            item_id=payload.get("itemID", ""),
            item_description=payload.get("itemDescription", ""),
            # Float, not int: spec marks itemQuantity as integer but real grocery
            # POS systems send fractional kg (e.g. "1.250").
            item_quantity=float(payload.get("itemQuantity", 0) or 0),
            item_unit_price=float(payload.get("itemUnitPrice", 0.0) or 0.0),
            total_amount=float(payload.get("totalAmount", 0.0) or 0.0),
            scan_attribute=payload.get("scanAttribute", "None"),
            item_attribute=payload.get("itemAttribute", "None"),
            discount_type=payload.get("discountType", "NoLineDiscount"),
            discount=float(payload.get("discount", 0.0) or 0.0),
            granted_by=payload.get("grantedBy", ""),
        )


class PaymentLine(BaseModel):
    line_timestamp: Optional[str] = None
    line_number: int = 0
    line_attribute: str = "None"
    payment_description: str = ""
    amount: float = 0.0
    card_type: str = ""
    payment_type_id: str = ""
    approval_code: str = ""
    card_number: str = ""

    @classmethod
    def from_nukkad(cls, payload: dict) -> PaymentLine:
        return cls(
            line_timestamp=payload.get("lineTimeStamp"),
            line_number=payload.get("lineNumber", 0),
            line_attribute=payload.get("lineAttribute", "None"),
            payment_description=payload.get("paymentDescription", ""),
            amount=float(payload.get("amount", 0.0) or 0.0),
            card_type=payload.get("cardType", ""),
            payment_type_id=payload.get("paymentTypeID", ""),
            approval_code=payload.get("approvalCode", ""),
            card_number=payload.get("cardNo", ""),
        )


class TotalLine(BaseModel):
    line_timestamp: Optional[str] = None
    line_number: int = 0
    line_attribute: str = ""
    description: str = ""
    amount: float = 0.0

    @classmethod
    def from_nukkad(cls, payload: dict) -> TotalLine:
        return cls(
            line_timestamp=payload.get("lineTimeStamp"),
            line_number=payload.get("lineNumber", 0),
            line_attribute=payload.get("lineAttribute", ""),
            description=payload.get("totalDescription", ""),
            amount=float(payload.get("amount", 0.0) or 0.0),
        )


class TransactionEvent(BaseModel):
    line_timestamp: Optional[str] = None
    line_attribute: str = ""
    event_description: str = ""

    @classmethod
    def from_nukkad(cls, payload: dict) -> TransactionEvent:
        return cls(
            line_timestamp=payload.get("lineTimeStamp"),
            line_attribute=payload.get("lineAttribute", ""),
            event_description=payload.get("eventDescription", ""),
        )


class TransactionSession(BaseModel):
    id: str
    store_id: str
    store_name: str = ""
    pos_terminal_no: str = ""
    display_pos_label: str = ""
    seller_window_id: str = ""
    cashier_id: str = ""
    debitor: str = ""
    transaction_type: str = "CompletedNormally"
    employee_purchase: bool = False
    outside_opening_hours: str = "InsideOpeningHours"
    source: str = "push_assembled"
    status: str = "open"
    started_at: Optional[str] = None
    last_event_at: Optional[str] = None
    committed_at: Optional[datetime] = None
    bill_number: str = ""
    transaction_number: str = ""
    is_previous_transaction: bool = False
    linked_transaction_id: str = ""
    items: list[SaleLine] = Field(default_factory=list)
    payments: list[PaymentLine] = Field(default_factory=list)
    totals: list[TotalLine] = Field(default_factory=list)
    events: list[TransactionEvent] = Field(default_factory=list)
    risk_level: str = "Low"
    triggered_rules: list[str] = Field(default_factory=list)
    cv_non_seller_present: Optional[bool] = None
    cv_receipt_detected: Optional[bool] = None
    cv_non_seller_count: int = 0
    cv_confidence: str = ""
    camera_id: str = ""
    device_id: str = ""
    snippet_path: str = ""
    notes: str = ""

    @property
    def pos_terminal(self) -> str:
        return self.pos_terminal_no or self.display_pos_label


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: gen_id("ALT"))
    transaction_id: str = ""
    store_id: str = ""
    store_name: str = ""
    pos_terminal_no: str = ""
    display_pos_label: str = ""
    cashier_id: str = ""
    risk_level: str = "Medium"
    triggered_rules: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)
    status: str = "new"
    resolved_by: str = ""
    resolved_at: Optional[datetime] = None
    remarks: Optional[str] = None
    camera_id: str = ""
    cv_window_start: Optional[datetime] = None
    cv_window_end: Optional[datetime] = None
    device_id: str = ""
    snippet_path: str = ""
    source: str = "rule"
    cv_confidence: str = ""
    # BRD 13.h — RLCC operator can mark a transaction as "this was a manual
    # paper bill, not through EPOS." Used to dismiss bill-not-generated
    # alerts that are actually legitimate manual-bill workflows.
    manual_bill: bool = False

    @property
    def pos_zone(self) -> str:
        return self.display_pos_label or self.pos_terminal_no


class CVWindow(BaseModel):
    pos_zone: str
    camera_id: str
    store_id: str = ""
    window_start: datetime
    window_end: datetime
    seller_present_pct: float = 0.0
    non_seller_present_pct: float = 0.0
    non_seller_count_max: int = 0
    bill_motion_detected: bool = False
    bill_bg_change_detected: bool = False
    frame_count: int = 0


class TimelineEvent(BaseModel):
    ts: str
    source: str
    type: str
    data: dict = Field(default_factory=dict)
