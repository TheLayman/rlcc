from datetime import datetime, timezone, timedelta
from backend.models import TransactionSession
from backend.cv_consumer import CVConsumer
from backend.config import Config


def correlate(txn: TransactionSession, cv_consumer: CVConsumer, config: Config) -> TransactionSession:
    seller_window_id = f"{txn.store_id}_{txn.pos_terminal}"
    camera = config.get_camera_by_seller_window(seller_window_id)
    if not camera or not camera.pos_zones:
        txn.cv_confidence = "UNMAPPED"
        return txn

    pos_zone = camera.pos_zones[0].zone_id

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
        return txn

    total_frames = sum(w.frame_count for w in windows)
    if total_frames == 0:
        txn.cv_confidence = "UNAVAILABLE"
        return txn

    non_seller_pct = sum(w.non_seller_present_pct * w.frame_count for w in windows) / total_frames
    bill_motion = any(w.bill_motion_detected for w in windows)
    bill_bg = any(w.bill_bg_change_detected for w in windows)
    max_non_seller = max(w.non_seller_count_max for w in windows)

    txn.cv_non_seller_present = non_seller_pct > 0.3
    txn.cv_non_seller_count = max_non_seller
    txn.cv_receipt_detected = bill_motion or bill_bg
    txn.cv_confidence = "REDUCED" if camera.multi_pos else "HIGH"
    txn.camera_id = camera.camera_id
    txn.device_id = camera.xprotect_device_id
    return txn
