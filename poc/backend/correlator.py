from __future__ import annotations

from datetime import datetime, timedelta

from backend.config import Config, normalize_terminal
from backend.cv_consumer import CVConsumer
from backend.models import TransactionSession


def correlate(txn: TransactionSession, cv_consumer: CVConsumer, config: Config) -> TransactionSession:
    camera = config.get_camera_by_terminal(txn.store_id, txn.pos_terminal_no)
    if not camera:
        txn.cv_confidence = "UNMAPPED"
        return txn

    pos_zone = camera.pos_zones[0].zone_id if camera.pos_zones else normalize_terminal(txn.pos_terminal_no)

    try:
        start = datetime.fromisoformat(txn.started_at.replace("Z", "+00:00")) if txn.started_at else None
        end = txn.committed_at
    except (ValueError, AttributeError):
        txn.cv_confidence = "UNAVAILABLE"
        return txn

    if not start or not end:
        txn.cv_confidence = "UNAVAILABLE"
        return txn

    start_padded = start - timedelta(seconds=3)
    end_padded = end + timedelta(seconds=3)
    windows = cv_consumer.get_windows(pos_zone, start_padded, end_padded)

    if not windows:
        txn.cv_confidence = "UNAVAILABLE"
        txn.camera_id = camera.camera_id
        txn.device_id = camera.xprotect_device_id
        return txn

    total_frames = sum(window.frame_count for window in windows)
    if total_frames == 0:
        txn.cv_confidence = "UNAVAILABLE"
        return txn

    non_seller_pct = sum(window.non_seller_present_pct * window.frame_count for window in windows) / total_frames
    bill_motion = any(window.bill_motion_detected for window in windows)
    bill_bg = any(window.bill_bg_change_detected for window in windows)
    max_non_seller = max(window.non_seller_count_max for window in windows)

    txn.cv_non_seller_present = non_seller_pct > 0.3
    txn.cv_non_seller_count = max_non_seller
    txn.cv_receipt_detected = bill_motion or bill_bg
    txn.cv_confidence = "REDUCED" if camera.multi_pos else "HIGH"
    txn.camera_id = camera.camera_id
    txn.device_id = camera.xprotect_device_id
    txn.display_pos_label = camera.display_pos_label
    txn.seller_window_id = camera.seller_window_key
    return txn
