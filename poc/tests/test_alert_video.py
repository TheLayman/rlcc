from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

import backend.main as main_module
from backend.config import CameraEntry, PosZoneConfig
from backend.main import app
from backend.models import Alert, TotalLine, TransactionSession


@pytest.mark.anyio
async def test_alert_video_falls_back_to_transaction_clip(monkeypatch, tmp_path):
    clip_path = tmp_path / "txn.mp4"
    clip_path.write_bytes(b"fake-video")

    alert = Alert(
        id="ALT-001",
        transaction_id="TXN-001",
        store_id="STORE",
        snippet_path="",
    )
    transaction = TransactionSession(
        id="TXN-001",
        store_id="STORE",
        snippet_path=str(clip_path),
    )

    monkeypatch.setattr(main_module, "_find_alert", lambda alert_id: alert if alert_id == "ALT-001" else None)
    monkeypatch.setattr(main_module, "_find_transaction", lambda txn_id: transaction if txn_id == "TXN-001" else None)
    monkeypatch.setattr(main_module.deps.video_manager, "clip_exists", lambda path: True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/alerts/ALT-001/video")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == clip_path.read_bytes()


@pytest.mark.anyio
async def test_transaction_video_is_served_inline(monkeypatch, tmp_path):
    clip_path = tmp_path / "txn.mp4"
    clip_path.write_bytes(b"fake-video")

    transaction = TransactionSession(
        id="TXN-002",
        store_id="STORE",
        snippet_path=str(clip_path),
    )

    monkeypatch.setattr(main_module, "_find_transaction", lambda txn_id: transaction if txn_id == "TXN-002" else None)
    monkeypatch.setattr(main_module.deps.video_manager, "clip_exists", lambda path: True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/transactions/TXN-002/video")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["content-disposition"] == 'inline; filename="TXN-002.mp4"'


@pytest.mark.anyio
async def test_transaction_video_rejects_invalid_existing_clip(monkeypatch, tmp_path):
    clip_path = tmp_path / "txn.mp4"
    clip_path.write_bytes(b"broken-video")

    transaction = TransactionSession(
        id="TXN-003",
        store_id="STORE",
        snippet_path=str(clip_path),
    )

    monkeypatch.setattr(main_module, "_find_transaction", lambda txn_id: transaction if txn_id == "TXN-003" else None)
    monkeypatch.setattr(main_module, "_extract_transaction_clip", lambda current: "")
    monkeypatch.setattr(main_module.deps.video_manager, "clip_exists", lambda path: False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/transactions/TXN-003/video")

    assert response.status_code == 404
    assert transaction.snippet_path == ""


@pytest.mark.anyio
async def test_get_transaction_repairs_missing_media_from_mapping(monkeypatch):
    now = datetime.now(timezone.utc)
    txn = TransactionSession(
        id="POLL-STORE-BILL-001",
        store_id="STORE",
        pos_terminal_no="POS 2",
        display_pos_label="",
        source="poll_reconciled",
        status="committed",
        started_at=(now - timedelta(seconds=10)).isoformat(),
        committed_at=now,
        bill_number="BILL-001",
        transaction_number="BILL-001",
        totals=[TotalLine(line_attribute="TotalAmountToBePaid", amount=450.0)],
    )
    camera = CameraEntry(
        seller_window_id="STORE_POS2",
        store_id="STORE",
        pos_terminal_no="POS 2",
        display_pos_label="POS 2",
        camera_id="cam-store-02",
        rtsp_url="",
        xprotect_device_id="dev-02",
        multi_pos=False,
        pos_zones=[PosZoneConfig(zone_id="POS2")],
        enabled=True,
    )
    saved_updates: list[tuple[str, dict]] = []

    monkeypatch.setattr(main_module, "_find_transaction", lambda txn_id: txn if txn_id == txn.id else None)
    monkeypatch.setattr(main_module.deps.config, "get_camera_by_terminal", lambda store_id, pos_terminal_no: camera)
    monkeypatch.setattr(main_module, "_extract_transaction_clip", lambda current: "/tmp/recovered.mp4")
    monkeypatch.setattr(main_module, "_save_transaction_updates", lambda txn_id, updates: saved_updates.append((txn_id, updates)))
    monkeypatch.setattr(main_module, "_repair_alert_media_for_transaction", lambda current: False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/transactions/{txn.id}")

    assert response.status_code == 200
    payload = response.json()["transaction"]
    assert payload["cam_id"] == "cam-store-02"
    assert payload["pos_id"] == "POS 2"
    assert payload["clip_url"] == f"/api/transactions/{txn.id}/video"
    assert saved_updates == [
        (
            txn.id,
            {
                "store_name": "STORE",
                "display_pos_label": "POS 2",
                "camera_id": "cam-store-02",
                "device_id": "dev-02",
                "seller_window_id": "STORE_POS2",
                "snippet_path": "/tmp/recovered.mp4",
            },
        )
    ]
