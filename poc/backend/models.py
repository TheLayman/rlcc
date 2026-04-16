from __future__ import annotations
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional
import uuid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def gen_id(prefix: str = "ALT") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


class SaleLine(BaseModel):
    line_timestamp: Optional[str] = None
    line_number: int = 0
    item_id: str = ""
    item_description: str = ""
    item_quantity: int = 0
    item_unit_price: float = 0.0
    total_amount: float = 0.0
    scan_attribute: str = "None"
    item_attribute: str = "None"
    discount_type: str = "NoLineDiscount"
    discount: float = 0.0
    granted_by: str = ""

    @classmethod
    def from_nukkad(cls, p: dict) -> SaleLine:
        return cls(
            line_timestamp=p.get("lineTimeStamp"),
            line_number=p.get("lineNumber", 0),
            item_id=p.get("itemID", ""),
            item_description=p.get("itemDescription", ""),
            item_quantity=p.get("itemQuantity", 0),
            item_unit_price=p.get("itemUnitPrice", 0.0),
            total_amount=p.get("totalAmount", 0.0),
            scan_attribute=p.get("scanAttribute", "None"),
            item_attribute=p.get("itemAttribute", "None"),
            discount_type=p.get("discountType", "NoLineDiscount"),
            discount=p.get("discount", 0.0),
            granted_by=p.get("grantedBy", ""),
        )


class PaymentLine(BaseModel):
    line_timestamp: Optional[str] = None
    line_number: int = 0
    line_attribute: str = "None"
    payment_description: str = ""
    amount: float = 0.0
    card_type: str = ""
    payment_type_id: str = ""

    @classmethod
    def from_nukkad(cls, p: dict) -> PaymentLine:
        return cls(
            line_timestamp=p.get("lineTimeStamp"),
            line_number=p.get("lineNumber", 0),
            line_attribute=p.get("lineAttribute", "None"),
            payment_description=p.get("paymentDescription", ""),
            amount=p.get("amount", 0.0),
            card_type=p.get("cardType", ""),
            payment_type_id=p.get("paymentTypeID", ""),
        )


class TotalLine(BaseModel):
    line_attribute: str = ""
    description: str = ""
    amount: float = 0.0

    @classmethod
    def from_nukkad(cls, p: dict) -> TotalLine:
        return cls(
            line_attribute=p.get("lineAttribute", ""),
            description=p.get("totalDescription", ""),
            amount=p.get("amount", 0.0),
        )


class TransactionEvent(BaseModel):
    line_timestamp: Optional[str] = None
    line_attribute: str = ""
    event_description: str = ""

    @classmethod
    def from_nukkad(cls, p: dict) -> TransactionEvent:
        return cls(
            line_timestamp=p.get("lineTimeStamp"),
            line_attribute=p.get("lineAttribute", ""),
            event_description=p.get("eventDescription", ""),
        )


class TransactionSession(BaseModel):
    id: str
    store_id: str
    pos_terminal: str = ""
    cashier_id: str = ""
    transaction_type: str = "CompletedNormally"
    employee_purchase: bool = False
    outside_opening_hours: str = "InsideOpeningHours"
    source: str = "push_assembled"
    status: str = "open"
    started_at: Optional[str] = None
    committed_at: Optional[datetime] = None
    bill_number: str = ""
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


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: gen_id("ALT"))
    transaction_id: str = ""
    store_id: str = ""
    pos_zone: str = ""
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


class CVWindow(BaseModel):
    pos_zone: str
    camera_id: str
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
