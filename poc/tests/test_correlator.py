from datetime import datetime, timezone, timedelta
from backend.correlator import correlate
from backend.cv_consumer import CVConsumer
from backend.config import Config, CameraEntry, PosZoneConfig
from backend.models import TransactionSession, CVWindow


def _make_cv_consumer_with_window(pos_zone, camera_id, non_seller_pct=0.8, bill_motion=True):
    cv = CVConsumer.__new__(CVConsumer)
    cv.windows = {}
    cv.latest = {}
    cv._accum = {}
    now = datetime.now(timezone.utc)
    window = CVWindow(
        pos_zone=pos_zone, camera_id=camera_id,
        window_start=now - timedelta(seconds=30), window_end=now,
        seller_present_pct=0.9, non_seller_present_pct=non_seller_pct,
        non_seller_count_max=2, bill_motion_detected=bill_motion,
        bill_bg_change_detected=False, frame_count=180,
    )
    cv.windows[CVConsumer.window_key(camera_id, pos_zone)] = [window]
    return cv, now


def _make_config_with_camera(seller_window_id, camera_id, pos_zone, multi_pos=False):
    config = Config.__new__(Config)
    config.cameras = [
        CameraEntry(
            seller_window_id=seller_window_id, store_id=seller_window_id.split("_")[0],
            pos_terminal_no=seller_window_id.split("_")[1] if "_" in seller_window_id else "",
            display_pos_label=seller_window_id.split("_")[1] if "_" in seller_window_id else "",
            camera_id=camera_id, rtsp_url="", xprotect_device_id="",
            multi_pos=multi_pos,
            pos_zones=[PosZoneConfig(zone_id=pos_zone, seller_zone=[], bill_zone=[])],
        )
    ]
    return config


def test_correlate_with_cv_data():
    cv, now = _make_cv_consumer_with_window("POS3", "cam-rambandi-01")
    config = _make_config_with_camera("NDCIN1223_POS 3", "cam-rambandi-01", "POS3")
    txn = TransactionSession(
        id="TXN-001", store_id="NDCIN1223", pos_terminal_no="POS 3",
        source="push_assembled",
        started_at=(now - timedelta(seconds=25)).isoformat(),
        committed_at=now,
    )
    result = correlate(txn, cv, config)
    assert result.cv_non_seller_present is True
    assert result.cv_receipt_detected is True
    assert result.cv_confidence == "HIGH"


def test_correlate_no_camera():
    cv, now = _make_cv_consumer_with_window("POS3", "cam-01")
    config = Config.__new__(Config)
    config.cameras = []
    txn = TransactionSession(id="TXN-002", store_id="UNKNOWN", pos_terminal_no="POS 1", source="push_assembled")
    result = correlate(txn, cv, config)
    assert result.cv_confidence == "UNMAPPED"


def test_correlate_multi_pos():
    cv, now = _make_cv_consumer_with_window("POS3", "cam-01")
    config = _make_config_with_camera("STORE_POS 3", "cam-01", "POS3", multi_pos=True)
    txn = TransactionSession(
        id="TXN-003", store_id="STORE", pos_terminal_no="POS 3",
        source="push_assembled",
        started_at=(now - timedelta(seconds=25)).isoformat(),
        committed_at=now,
    )
    result = correlate(txn, cv, config)
    assert result.cv_confidence == "REDUCED"


def test_correlate_ignores_windows_from_other_camera():
    cv = CVConsumer.__new__(CVConsumer)
    cv.windows = {}
    cv.latest = {}
    cv._accum = {}
    now = datetime.now(timezone.utc)
    cv.windows[CVConsumer.window_key("cam-other", "POS1")] = [
        CVWindow(
            pos_zone="POS1",
            camera_id="cam-other",
            window_start=now - timedelta(seconds=30),
            window_end=now,
            seller_present_pct=0.9,
            non_seller_present_pct=0.8,
            non_seller_count_max=2,
            bill_motion_detected=True,
            bill_bg_change_detected=False,
            frame_count=180,
        )
    ]
    config = _make_config_with_camera("STORE_POS 1", "cam-target", "POS1")
    txn = TransactionSession(
        id="TXN-004",
        store_id="STORE",
        pos_terminal_no="POS 1",
        source="push_assembled",
        started_at=(now - timedelta(seconds=25)).isoformat(),
        committed_at=now,
    )

    result = correlate(txn, cv, config)

    assert result.cv_confidence == "UNAVAILABLE"
    assert result.camera_id == "cam-target"


def test_correlate_handles_naive_ist_started_at():
    """Live-prod Nukkad sends naive timestamps that the receiver assumes are
    IST.  Correlator must apply the same IST→UTC conversion before window
    matching, otherwise the comparison either drifts 5.5 hours or raises
    TypeError (naive vs tz-aware).
    """
    # CV window is at "now" in UTC.  Naive started_at expressed in IST should
    # land inside the window after conversion.
    now_utc = datetime.now(timezone.utc)
    cv = CVConsumer.__new__(CVConsumer)
    cv.windows = {}
    cv.latest = {}
    cv._accum = {}
    window = CVWindow(
        pos_zone="POS1",
        camera_id="cam-x",
        window_start=now_utc - timedelta(seconds=30),
        window_end=now_utc + timedelta(seconds=30),
        seller_present_pct=0.9,
        non_seller_present_pct=0.7,
        non_seller_count_max=1,
        bill_motion_detected=False,
        bill_bg_change_detected=False,
        frame_count=180,
    )
    cv.windows[CVConsumer.window_key("cam-x", "POS1")] = [window]

    config = _make_config_with_camera("STORE_POS 1", "cam-x", "POS1")

    # Construct a naive IST timestamp that corresponds to "now" in UTC.
    ist_offset = timedelta(hours=5, minutes=30)
    naive_ist_started = (now_utc + ist_offset).replace(tzinfo=None).isoformat()

    txn = TransactionSession(
        id="TXN-IST-001",
        store_id="STORE",
        pos_terminal_no="POS 1",
        source="push_assembled",
        started_at=naive_ist_started,   # naive — no tz suffix
        committed_at=now_utc,
    )

    result = correlate(txn, cv, config)

    # If the bug is present (no IST conversion), this would either be
    # UNAVAILABLE (5.5h drift means no window overlap) or raise TypeError.
    assert result.cv_confidence == "HIGH"
    assert result.cv_non_seller_present is True
