from __future__ import annotations

from datetime import timedelta

from backend.config import Config, normalize_terminal
from backend.cv_consumer import CVConsumer
from backend.models import TransactionSession
from backend.persistence import parse_dt

# CV-window correlation outcomes. Distinct from the alert-escalation levels
# in backend/confidence.py (LOW/MEDIUM/HIGH/VERY_HIGH).
CV_CONFIDENCE_HIGH = "HIGH"
CV_CONFIDENCE_REDUCED = "REDUCED"
CV_CONFIDENCE_UNMAPPED = "UNMAPPED"
CV_CONFIDENCE_UNAVAILABLE = "UNAVAILABLE"


def correlate(txn: TransactionSession, cv_consumer: CVConsumer, config: Config) -> TransactionSession:
    camera = config.get_camera_by_terminal(txn.store_id, txn.pos_terminal_no)
    if not camera:
        txn.cv_confidence = CV_CONFIDENCE_UNMAPPED
        return txn

    pos_zone = camera.pos_zones[0].zone_id if camera.pos_zones else normalize_terminal(txn.pos_terminal_no)

    # parse_dt normalizes naive Nukkad timestamps IST→UTC per BACKEND_DESIGN §11,
    # so window comparison doesn't drift or raise on naive vs tz-aware datetimes.
    start = parse_dt(txn.started_at)
    end = parse_dt(txn.committed_at)

    if not start or not end:
        txn.cv_confidence = CV_CONFIDENCE_UNAVAILABLE
        return txn

    start_padded = start - timedelta(seconds=3)
    end_padded = end + timedelta(seconds=3)
    windows = cv_consumer.get_windows(camera.camera_id, pos_zone, start_padded, end_padded)

    if not windows:
        txn.cv_confidence = CV_CONFIDENCE_UNAVAILABLE
        txn.camera_id = camera.camera_id
        txn.device_id = camera.xprotect_device_id
        return txn

    total_frames = sum(window.frame_count for window in windows)
    if total_frames == 0:
        txn.cv_confidence = CV_CONFIDENCE_UNAVAILABLE
        return txn

    non_seller_pct = sum(window.non_seller_present_pct * window.frame_count for window in windows) / total_frames
    bill_motion = any(window.bill_motion_detected for window in windows)
    bill_bg = any(window.bill_bg_change_detected for window in windows)
    screen_motion = any(window.screen_motion_detected for window in windows)
    screen_bg = any(window.screen_bg_change_detected for window in windows)
    bill_hand = any(window.bill_hand_present for window in windows)
    screen_hand = any(window.screen_hand_present for window in windows)
    max_non_seller = max(window.non_seller_count_max for window in windows)

    txn.cv_non_seller_present = non_seller_pct > 0.3
    txn.cv_non_seller_count = max_non_seller
    txn.cv_receipt_detected = bill_motion or bill_bg
    txn.cv_bill_hand_present = bill_hand
    txn.cv_screen_motion = screen_motion
    txn.cv_screen_bg = screen_bg
    txn.cv_screen_hand_present = screen_hand
    txn.cv_confidence = CV_CONFIDENCE_REDUCED if camera.multi_pos else CV_CONFIDENCE_HIGH
    txn.camera_id = camera.camera_id
    txn.device_id = camera.xprotect_device_id
    txn.display_pos_label = camera.display_pos_label
    txn.seller_window_id = camera.seller_window_key
    return txn
