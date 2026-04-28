"""Unit tests for backend.confidence.compute_missing_pos_confidence."""
from datetime import datetime, timedelta, timezone

from backend.confidence import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_VERY_HIGH,
    RULE_BASE_MISSING_POS,
    RULE_OFF_BOOK_HANDLING,
    RULE_OFF_BOOK_RECEIPT,
    compute_missing_pos_confidence,
)


def _now() -> datetime:
    return datetime(2026, 4, 28, 10, 0, 0, tzinfo=timezone.utc)


def _activity(label: str, *, age_seconds: float = 0.0) -> dict:
    ts = (_now() - timedelta(seconds=age_seconds)).isoformat().replace("+00:00", "Z")
    return {
        "ts": ts,
        "store_id": "NDCIN1231",
        "camera_id": "cam-x",
        "pos_zone": "POS1",
        "seller_activity": label,
        "confidence": 0.82,
    }


def _signal(*, bill_motion: bool = False, bill_bg: bool = False, age_seconds: float = 0.0) -> dict:
    ts = (_now() - timedelta(seconds=age_seconds)).isoformat().replace("+00:00", "Z")
    return {
        "ts": ts,
        "store_id": "NDCIN1231",
        "camera_id": "cam-x",
        "zones": [
            {
                "pos_zone": "POS1",
                "seller": True,
                "bill_motion": bill_motion,
                "bill_bg": bill_bg,
            }
        ],
        "non_seller_count": 1,
        "non_seller_present": True,
    }


def test_low_when_dwell_short_and_no_extras():
    verdict = compute_missing_pos_confidence(
        started_at=_now() - timedelta(seconds=10),
        pos_zone="POS1",
        latest_activity=None,
        latest_signal=_signal(),
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_LOW
    assert verdict.risk_level == "Low"
    assert verdict.rule_ids == [RULE_BASE_MISSING_POS]


def test_medium_when_dwell_exceeds_threshold():
    verdict = compute_missing_pos_confidence(
        started_at=_now() - timedelta(seconds=45),
        pos_zone="POS1",
        latest_activity=None,
        latest_signal=_signal(),
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_MEDIUM
    assert verdict.risk_level == "Medium"


def test_high_for_handling_cash():
    verdict = compute_missing_pos_confidence(
        started_at=_now() - timedelta(seconds=20),
        pos_zone="POS1",
        latest_activity=_activity("handling_cash", age_seconds=2),
        latest_signal=_signal(),
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_HIGH
    assert RULE_OFF_BOOK_HANDLING in verdict.rule_ids


def test_high_for_handling_item():
    verdict = compute_missing_pos_confidence(
        started_at=_now() - timedelta(seconds=12),
        pos_zone="POS1",
        latest_activity=_activity("handling_item", age_seconds=3),
        latest_signal=_signal(),
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_HIGH


def test_very_high_when_bill_zone_active():
    verdict = compute_missing_pos_confidence(
        started_at=_now() - timedelta(seconds=40),
        pos_zone="POS1",
        latest_activity=_activity("idle", age_seconds=2),
        latest_signal=_signal(bill_motion=True, age_seconds=1),
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_VERY_HIGH
    assert RULE_OFF_BOOK_RECEIPT in verdict.rule_ids


def test_stale_activity_does_not_escalate():
    verdict = compute_missing_pos_confidence(
        started_at=_now() - timedelta(seconds=20),
        pos_zone="POS1",
        latest_activity=_activity("handling_cash", age_seconds=30),
        latest_signal=_signal(),
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_LOW


def test_stale_signal_does_not_escalate_to_very_high():
    verdict = compute_missing_pos_confidence(
        started_at=_now() - timedelta(seconds=40),
        pos_zone="POS1",
        latest_activity=None,
        latest_signal=_signal(bill_motion=True, age_seconds=10),
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_MEDIUM


def test_bill_activity_for_other_zone_is_ignored():
    other_zone_signal = {
        "ts": _now().isoformat().replace("+00:00", "Z"),
        "zones": [{"pos_zone": "POS4", "bill_motion": True, "bill_bg": True}],
    }
    verdict = compute_missing_pos_confidence(
        started_at=_now() - timedelta(seconds=40),
        pos_zone="POS1",
        latest_activity=None,
        latest_signal=other_zone_signal,
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_MEDIUM


def test_very_high_takes_precedence_over_high():
    verdict = compute_missing_pos_confidence(
        started_at=_now() - timedelta(seconds=20),
        pos_zone="POS1",
        latest_activity=_activity("handling_cash", age_seconds=2),
        latest_signal=_signal(bill_bg=True, age_seconds=1),
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_VERY_HIGH
    assert RULE_OFF_BOOK_RECEIPT in verdict.rule_ids
    assert RULE_OFF_BOOK_HANDLING not in verdict.rule_ids


def test_naive_started_at_is_treated_as_utc():
    verdict = compute_missing_pos_confidence(
        started_at=datetime(2026, 4, 28, 9, 59, 0),  # 60s before _now() (UTC)
        pos_zone="POS1",
        latest_activity=None,
        latest_signal=_signal(),
        now=_now(),
    )
    assert verdict.level == CONFIDENCE_MEDIUM
