"""Multi-POS-per-camera resolution tests.

Cafe Niloufer (NDCIN1422) is the canonical case: one camera, two physical
POS terminals (POS 1 + POS 2), one shared zone. Per the POC contract,
camera lookup is store-keyed and per-POS attribution is preserved on the
transaction itself (display_pos_label, pos_terminal_no) but not used for
camera/zone selection.
"""
from backend.config import Config, CameraEntry, PosZoneConfig, StoreEntry
from backend.models import TransactionSession


def _niloufer_config() -> Config:
    cfg = Config.__new__(Config)
    cfg.stores = [StoreEntry(cin="NDCIN1422", name="Cafe Niloufer", pos_system="Posifly-Dino")]
    cfg.cameras = [
        CameraEntry(
            seller_window_id="NDCIN1422_POS1",
            store_id="NDCIN1422",
            pos_terminal_no="POS 1",
            display_pos_label="POS 1",
            camera_id="cam-cafeniloufer-01",
            rtsp_url="rtsp://x",
            xprotect_device_id="",
            multi_pos=True,
            pos_zones=[PosZoneConfig(zone_id="POS1")],
            enabled=True,
            match_any_pos_in_store=True,
            nukkad_pos_aliases=[],
        ),
    ]
    cfg.rules = {}
    return cfg


def test_get_camera_for_store_finds_niloufer():
    cfg = _niloufer_config()
    cam = cfg.get_camera_for_store("NDCIN1422")
    assert cam is not None
    assert cam.camera_id == "cam-cafeniloufer-01"


def test_get_camera_for_store_returns_none_when_no_camera():
    cfg = _niloufer_config()
    assert cfg.get_camera_for_store("DOES_NOT_EXIST") is None


def test_get_camera_for_store_skips_disabled():
    cfg = _niloufer_config()
    cfg.cameras[0].enabled = False
    assert cfg.get_camera_for_store("NDCIN1422") is None


def test_hydrate_transaction_preserves_per_pos_label():
    """A POS 2 transaction at Niloufer must keep display_pos_label='POS 2',
    not the camera's primary 'POS 1'."""
    import backend.deps as deps
    deps.config = _niloufer_config()

    from backend.receiver import _hydrate_transaction
    txn = TransactionSession(
        id="sess-pos2",
        store_id="NDCIN1422",
        pos_terminal_no="POS 2",
        display_pos_label="POS 2",
        seller_window_id="",
        cashier_id="",
    )
    hydrated = _hydrate_transaction(txn)
    assert hydrated.display_pos_label == "POS 2", \
        f"expected POS 2, got {hydrated.display_pos_label!r}"
    assert hydrated.camera_id == "cam-cafeniloufer-01"
    assert hydrated.store_name == "Cafe Niloufer"


def test_hydrate_transaction_pos1_also_resolves_to_same_camera():
    """Both POS 1 and POS 2 at NDCIN1422 must hydrate to cam-cafeniloufer-01."""
    import backend.deps as deps
    deps.config = _niloufer_config()
    from backend.receiver import _hydrate_transaction

    txn = TransactionSession(
        id="sess-pos1",
        store_id="NDCIN1422",
        pos_terminal_no="POS 1",
        display_pos_label="POS 1",
        seller_window_id="",
        cashier_id="",
    )
    hydrated = _hydrate_transaction(txn)
    assert hydrated.camera_id == "cam-cafeniloufer-01"
    assert hydrated.display_pos_label == "POS 1"


def test_hydrate_falls_back_to_camera_label_when_session_label_missing():
    """If Nukkad's payload didn't carry a POS label and the session has none,
    we still produce a usable display_pos_label by falling back to the
    camera's primary."""
    import backend.deps as deps
    deps.config = _niloufer_config()
    from backend.receiver import _hydrate_transaction

    txn = TransactionSession(
        id="sess-blank",
        store_id="NDCIN1422",
        pos_terminal_no="",
        display_pos_label="",
        seller_window_id="",
        cashier_id="",
    )
    hydrated = _hydrate_transaction(txn)
    # Empty-pos_terminal_no falls through to camera primary
    assert hydrated.display_pos_label == "POS 1"
