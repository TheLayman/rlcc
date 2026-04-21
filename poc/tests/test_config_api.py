import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import backend.deps as deps
from backend.main import app

POC_DIR = Path(__file__).parent.parent
CONFIG_DIR = POC_DIR / "config"
STORES_PATH = CONFIG_DIR / "stores.json"
CAMERA_PATH = CONFIG_DIR / "camera_mapping.json"


@pytest.fixture
def restore_config_files():
    original_stores = STORES_PATH.read_text(encoding="utf-8")
    original_cameras = CAMERA_PATH.read_text(encoding="utf-8")
    yield
    STORES_PATH.write_text(original_stores, encoding="utf-8")
    CAMERA_PATH.write_text(original_cameras, encoding="utf-8")
    deps.config.reload()


@pytest.mark.anyio
async def test_update_stores_rejects_duplicate_store_ids(restore_config_files):
    transport = ASGITransport(app=app)
    payload = {
        "stores": [
            {"cin": "NDCIN1223", "name": "Ram Ki Bandi", "pos_system": "Posifly-Dino"},
            {"cin": "NDCIN1223", "name": "Duplicate", "pos_system": "Posifly-Dino"},
        ]
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/stores", json=payload)

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "Duplicate store ID" in response.json()["message"]


@pytest.mark.anyio
async def test_update_stores_persists_store_catalog(restore_config_files):
    transport = ASGITransport(app=app)
    payload = {
        "stores": [
            {"cin": "NDCIN1223", "name": "Ram Ki Bandi", "pos_system": "Posifly-Dino", "operator": "DIL"},
            {"cin": "NDCIN1231", "name": "Nizami Daawat", "pos_system": "Posifly-Dino", "operator": "Zoha Foods"},
            {"cin": "NDCIN1227", "name": "KFC", "pos_system": "Posifly-Dino", "operator": "DIL"},
            {"cin": "NDCIN1228", "name": "Haldiram's-AeroPlaza", "pos_system": "Posifly-Dino", "operator": "Oam"},
        ]
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/stores", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert [store["cin"] for store in body["stores"]] == [
        "NDCIN1223",
        "NDCIN1231",
        "NDCIN1227",
        "NDCIN1228",
    ]

    stored = json.loads(STORES_PATH.read_text(encoding="utf-8"))
    assert stored[1]["name"] == "Nizami Daawat"
    assert deps.config.get_store("NDCIN1228").name == "Haldiram's-AeroPlaza"


@pytest.mark.anyio
async def test_update_camera_mapping_reports_cv_reload_success(monkeypatch, restore_config_files):
    import backend.camera_api as camera_api

    class FakeResponse:
        is_success = True
        status_code = 200
        text = ""

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str):
            assert url.endswith("/config/reload")
            return FakeResponse()

    monkeypatch.setattr(camera_api.httpx, "AsyncClient", FakeAsyncClient)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        current = await client.get("/api/camera-mapping")
        response = await client.post("/api/camera-mapping", json={"cameras": current.json()["cameras"]})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["cv_reloaded"] is True
    assert body["cv_reload_error"] == ""
