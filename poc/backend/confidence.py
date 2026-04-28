"""Confidence ladder for CV-driven alerts.

Implements the escalation rules described in CV_PIPELINE.md § "Confidence ladder":

    LOW        customer + seller, no POS event                  (Phase 1)
    MEDIUM     above + customer dwell > medium_dwell_seconds    (Phase 1)
    HIGH       above + seller_activity = handling_item|cash     (Phase 2)
    VERY HIGH  above + bill_zone activity but still no POS      (off-book receipt?)

Pure function — no app state, no I/O.  Caller passes already-fetched signals;
this module just returns the verdict so it is trivially unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_VERY_HIGH = "VERY_HIGH"

ACTIVITY_HANDLING_LABELS = {"handling_item", "handling_cash"}
ACTIVITY_RECEIPT_LABEL = "giving_receipt"

RULE_BASE_MISSING_POS = "24_missing_pos"
RULE_OFF_BOOK_HANDLING = "24a_off_book_handling"
RULE_OFF_BOOK_RECEIPT = "24b_off_book_receipt"


@dataclass
class ConfidenceVerdict:
    level: str
    risk_level: str
    rule_ids: list[str] = field(default_factory=list)
    reason: str = ""


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _activity_is_fresh(payload: dict | None, now: datetime, max_age_seconds: float) -> bool:
    if not payload:
        return False
    ts = _parse_iso(payload.get("ts"))
    if ts is None:
        return False
    return (now - ts).total_seconds() <= max_age_seconds


def _bill_zone_active(signal: dict | None, pos_zone: str) -> bool:
    if not signal:
        return False
    for zone in signal.get("zones", []):
        if zone.get("pos_zone") != pos_zone:
            continue
        if zone.get("bill_motion") or zone.get("bill_bg"):
            return True
    return False


def compute_missing_pos_confidence(
    *,
    started_at: datetime,
    pos_zone: str,
    latest_activity: dict | None,
    latest_signal: dict | None,
    now: datetime | None = None,
    medium_dwell_seconds: float = 30.0,
    activity_freshness_seconds: float = 10.0,
    signal_freshness_seconds: float = 5.0,
) -> ConfidenceVerdict:
    """Return the confidence verdict for a Missing-POS alert candidate.

    Caller is expected to have already established that there is no open POS
    session for this terminal (otherwise the alert wouldn't be raised at all).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    dwell = max(0.0, (now - started_at).total_seconds())

    activity_label = ""
    activity_age = float("inf")
    if _activity_is_fresh(latest_activity, now, activity_freshness_seconds):
        activity_label = (latest_activity or {}).get("seller_activity", "") or ""
        activity_ts = _parse_iso((latest_activity or {}).get("ts"))
        if activity_ts is not None:
            activity_age = (now - activity_ts).total_seconds()

    signal_fresh = _activity_is_fresh(latest_signal, now, signal_freshness_seconds)
    bill_active = signal_fresh and _bill_zone_active(latest_signal, pos_zone)

    rules: list[str] = [RULE_BASE_MISSING_POS]

    if bill_active:
        rules.append(RULE_OFF_BOOK_RECEIPT)
        return ConfidenceVerdict(
            level=CONFIDENCE_VERY_HIGH,
            risk_level="High",
            rule_ids=rules,
            reason=(
                f"co-presence dwell {dwell:.0f}s, bill-zone activity detected "
                "without a POS event (possible off-book receipt)"
            ),
        )

    if activity_label in ACTIVITY_HANDLING_LABELS:
        rules.append(RULE_OFF_BOOK_HANDLING)
        return ConfidenceVerdict(
            level=CONFIDENCE_HIGH,
            risk_level="High",
            rule_ids=rules,
            reason=(
                f"co-presence dwell {dwell:.0f}s, seller activity "
                f"'{activity_label}' observed {activity_age:.0f}s ago without a POS event"
            ),
        )

    if dwell > medium_dwell_seconds:
        return ConfidenceVerdict(
            level=CONFIDENCE_MEDIUM,
            risk_level="Medium",
            rule_ids=rules,
            reason=(
                f"co-presence dwell {dwell:.0f}s exceeds {medium_dwell_seconds:.0f}s "
                "with no POS event; seller activity not classified yet"
            ),
        )

    return ConfidenceVerdict(
        level=CONFIDENCE_LOW,
        risk_level="Low",
        rule_ids=rules,
        reason=f"co-presence dwell {dwell:.0f}s, no POS event yet",
    )
