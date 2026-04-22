import pytest
from httpx import ASGITransport, AsyncClient

import backend.main as main_module
from backend.main import app
from backend.models import Alert, TransactionSession


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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/transactions/TXN-002/video")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["content-disposition"] == 'inline; filename="TXN-002.mp4"'
