from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any

import httpx

from backend.config import Config, build_seller_window_id
from backend.models import PaymentLine, SaleLine, TotalLine, TransactionEvent, TransactionSession

IST = timezone(timedelta(hours=5, minutes=30))


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _first_present(*values: Any, default: Any = "") -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return default


def _store_id_from_bill(bill: dict) -> str:
    return str(
        _first_present(
            bill.get("nscin"),
            bill.get("ndcin"),
            bill.get("cin"),
            bill.get("storeIdentifier"),
        )
    )


def _terminal_from_bill(bill: dict) -> str:
    return str(
        _first_present(
            bill.get("terminalNo"),
            bill.get("terminalName"),
            bill.get("posTerminalNo"),
        )
    )


def _cashier_from_bill(bill: dict) -> str:
    cashier_details = bill.get("cashierDetails") or {}
    return str(
        _first_present(
            bill.get("cashierName"),
            cashier_details.get("cashierName"),
            bill.get("waiterName"),
            default="Unknown",
        )
    ).strip()


def _bill_datetime(bill: dict) -> datetime:
    bill_date = str(bill.get("billDate") or "").strip()
    bill_time = str(bill.get("billTime") or "").strip()
    sync_time = str(bill.get("billSyncTime") or "").strip()

    if bill_date and bill_time:
        return datetime.fromisoformat(f"{bill_date}T{bill_time}").replace(tzinfo=IST)

    if sync_time:
        return datetime.fromisoformat(sync_time.replace(" ", "T")).replace(tzinfo=IST)

    return datetime.now(IST)


def _polled_txn_id(store_id: str, bill_number: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", bill_number or "unknown").strip("-") or "unknown"
    return f"POLL-{store_id}-{slug}"


def _payment_attribute(mode: str) -> str:
    normalized = (mode or "").strip().lower()
    if "credit note" in normalized:
        return "CreditNotePayment"
    if "return" in normalized or "refund" in normalized:
        return "ReturnCash"
    if "upi" in normalized or any(name in normalized for name in ("phonepe", "gpay", "paytm", "bharatpe")):
        return "UPI"
    if "gift" in normalized:
        return "GiftCard"
    if "loyalty" in normalized:
        return "LoyaltyCard"
    if "card" in normalized or any(name in normalized for name in ("visa", "mastercard", "amex", "rupay")):
        return "CreditCard"
    if "cash" in normalized:
        return "Cash"
    return mode or "Unknown"


def _transaction_type_from_bill(bill: dict) -> str:
    if str(bill.get("isComplementary") or "").strip().lower() == "yes":
        return "Complementary"

    bill_type = str(bill.get("billType") or "").strip().lower()
    status = str(bill.get("status") or "").strip().lower()
    if bill.get("voidReason") or bill.get("cancelDate") or bill_type in {"cancel", "cancelled"} or status in {"cancel", "cancelled"}:
        return "Cancelled"

    return "CompletedNormally"


def _line_timestamp(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def map_bill_to_transaction(bill: dict, config: Config) -> TransactionSession:
    store_id = _store_id_from_bill(bill)
    pos_terminal_no = _terminal_from_bill(bill)
    bill_number = str(bill.get("billNo") or "")
    ts = _bill_datetime(bill)
    line_ts = _line_timestamp(ts)

    items: list[SaleLine] = []
    for index, item in enumerate(bill.get("items") or [], start=1):
        qty = _as_int(_first_present(item.get("qty"), item.get("quantity"), default=1), default=1)
        unit_price = _as_float(
            _first_present(
                item.get("sp"),
                item.get("price"),
                item.get("itemUnitPrice"),
                item.get("mrp"),
                item.get("netSP"),
                default=0.0,
            )
        )
        line_total = _as_float(
            _first_present(
                item.get("totAmt"),
                item.get("totalAmount"),
                item.get("amount"),
                default="",
            ),
            default=unit_price * max(qty, 1),
        )
        discount = _as_float(
            _first_present(
                item.get("discount"),
                item.get("discAmt"),
                default=0.0,
            )
        )
        items.append(
            SaleLine(
                line_timestamp=line_ts,
                line_number=index,
                item_id=str(
                    _first_present(
                        item.get("productCode"),
                        item.get("skuCode"),
                        item.get("barcode"),
                        item.get("pluNumber"),
                        item.get("id"),
                    )
                ),
                item_description=str(_first_present(item.get("name"), item.get("itemDescription"), default="")),
                item_quantity=max(qty, 0),
                item_unit_price=unit_price,
                total_amount=line_total,
                scan_attribute="None",
                item_attribute="None",
                discount_type="AutoGeneratedValue" if discount > 0 else "NoLineDiscount",
                discount=discount,
                granted_by="",
            )
        )

    payments: list[PaymentLine] = []
    for index, payment in enumerate(bill.get("payModes") or [], start=1):
        mode = str(_first_present(payment.get("mode"), payment.get("paymentDescription"), default="Unknown"))
        payments.append(
            PaymentLine(
                line_timestamp=line_ts,
                line_number=index,
                line_attribute=_payment_attribute(mode),
                payment_description=mode,
                amount=_as_float(payment.get("amt")),
                card_type=str(payment.get("cardType") or ""),
                payment_type_id=str(payment.get("tenderCode") or ""),
                approval_code=str(payment.get("approvalCode") or ""),
                card_number=str(payment.get("cardNo") or ""),
            )
        )

    totals: list[TotalLine] = []
    subtotal = _as_float(_first_present(bill.get("saleAmt"), bill.get("actualBillAmt"), bill.get("billAmt"), bill.get("netSaleAmt")))
    discount_total = _as_float(_first_present(bill.get("discAmt"), bill.get("totalDiscAmt"), default=0.0))
    tax_total = _as_float(_first_present(bill.get("taxAmnt"), default=0.0))
    rounding = _as_float(_first_present(bill.get("roundingAmnt"), default=0.0))
    grand_total = _as_float(
        _first_present(
            bill.get("billAmt"),
            bill.get("actualBillAmt"),
            bill.get("netSaleAmt"),
            bill.get("saleAmt"),
            default=0.0,
        )
    )

    if subtotal:
        totals.append(TotalLine(line_timestamp=line_ts, line_number=1, line_attribute="SubTotal", description="Sub total", amount=subtotal))
    if discount_total:
        totals.append(
            TotalLine(
                line_timestamp=line_ts,
                line_number=len(totals) + 1,
                line_attribute="TotalDiscount",
                description="Total discount",
                amount=discount_total,
            )
        )
    if tax_total:
        totals.append(TotalLine(line_timestamp=line_ts, line_number=len(totals) + 1, line_attribute="VAT", description="Tax", amount=tax_total))
    if rounding:
        totals.append(
            TotalLine(
                line_timestamp=line_ts,
                line_number=len(totals) + 1,
                line_attribute="Rounding",
                description="Rounding",
                amount=rounding,
            )
        )
    for charge in bill.get("charges") or []:
        totals.append(
            TotalLine(
                line_timestamp=line_ts,
                line_number=len(totals) + 1,
                line_attribute="Charge",
                description=str(charge.get("chargeName") or charge.get("name") or "Charge"),
                amount=_as_float(charge.get("amount")),
            )
        )
    totals.append(
        TotalLine(
            line_timestamp=line_ts,
            line_number=len(totals) + 1,
            line_attribute="TotalAmountToBePaid",
            description="Total amount to be paid",
            amount=grand_total,
        )
    )

    events: list[TransactionEvent] = []
    if bill.get("voidReason") or bill.get("cancelDate"):
        events.append(
            TransactionEvent(
                line_timestamp=line_ts,
                line_attribute="TransactionCancelled",
                event_description=str(_first_present(bill.get("voidReason"), bill.get("refundReason"), default="Cancelled bill")),
            )
        )

    transaction = TransactionSession(
        id=_polled_txn_id(store_id, bill_number),
        store_id=store_id,
        store_name=config.get_store_name(store_id),
        pos_terminal_no=pos_terminal_no,
        display_pos_label=pos_terminal_no,
        seller_window_id=build_seller_window_id(store_id, pos_terminal_no),
        cashier_id=_cashier_from_bill(bill),
        debitor=str(_first_present(bill.get("consumerName"), bill.get("consumerMobile"), default="")),
        transaction_type=_transaction_type_from_bill(bill),
        employee_purchase=False,
        outside_opening_hours="InsideOpeningHours",
        source="poll_reconciled",
        status="committed",
        started_at=line_ts,
        last_event_at=line_ts,
        committed_at=ts.astimezone(timezone.utc),
        bill_number=bill_number,
        transaction_number=bill_number,
        items=items,
        payments=payments,
        totals=totals,
        events=events,
        notes="Backfilled from sales API.",
    )

    camera = config.get_camera_by_terminal(store_id, pos_terminal_no)
    if camera:
        transaction.display_pos_label = camera.display_pos_label
        transaction.camera_id = camera.camera_id
        transaction.device_id = camera.xprotect_device_id
        transaction.seller_window_id = camera.seller_window_key

    return transaction


class SalesPoller:
    def __init__(self, *, api_url: str, api_token: str, config: Config, timeout_seconds: float = 30.0):
        self.api_url = (api_url or "").strip()
        self.api_token = (api_token or "").strip()
        self.config = config
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_url and self.api_token)

    def ensure_ready(self) -> None:
        missing: list[str] = []
        if not self.api_url:
            missing.append("EXTERNAL_SALES_URL")
        if not self.api_token:
            missing.append("EXTERNAL_SALES_HEADER_TOKEN")
        if missing:
            raise RuntimeError("Missing required sales API configuration: " + ", ".join(missing))

    async def _fetch_store_bills(self, *, store_id: str, start_ts: int, end_ts: int) -> list[dict]:
        bills: list[dict] = []
        page = 1

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            while True:
                response = await client.post(
                    self.api_url,
                    headers={"X-Nukkad-API-Token": self.api_token},
                    json={
                        "cin": store_id,
                        "from": str(start_ts),
                        "to": str(end_ts),
                        "pageNo": str(page),
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("response"):
                    break

                data = payload.get("data") or {}
                page_bills = data.get("bills") or []
                if not page_bills:
                    break
                bills.extend(page_bills)

                page_count = int(data.get("pageCount") or 1)
                if page >= page_count:
                    break
                page += 1

        return bills

    async def fetch_between(self, start: datetime, end: datetime) -> list[dict]:
        self.ensure_ready()

        if end <= start:
            return []

        start_ts = int(start.astimezone(IST).timestamp())
        end_ts = int(end.astimezone(IST).timestamp())
        all_bills: list[dict] = []
        for store in self.config.stores:
            all_bills.extend(await self._fetch_store_bills(store_id=store.cin, start_ts=start_ts, end_ts=end_ts))
        return all_bills

    async def fetch_historical(self, days: int) -> list[dict]:
        self.ensure_ready()

        now = datetime.now(IST)
        bills: list[dict] = []
        for day_offset in range(days, 0, -1):
            day_start = datetime.combine((now - timedelta(days=day_offset)).date(), time.min, tzinfo=IST)
            day_end = datetime.combine((now - timedelta(days=day_offset - 1)).date(), time.min, tzinfo=IST)
            bills.extend(await self.fetch_between(day_start, day_end))
        return bills
