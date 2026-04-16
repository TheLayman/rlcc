import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import Config
from backend.storage import Storage
from backend.assembler import TransactionAssembler
from backend.fraud import FraudEngine
from backend.ws import ConnectionManager

POC_DIR = Path(__file__).parent.parent
CONFIG_DIR = POC_DIR / "config"
DATA_DIR = POC_DIR / "data"

# Globals — shared across the app
config = Config(config_dir=str(CONFIG_DIR))
storage = Storage(data_dir=str(DATA_DIR))
assembler = TransactionAssembler()
fraud_engine = FraudEngine(config.rules)
ws_manager = ConnectionManager()


async def config_watcher():
    while True:
        await asyncio.sleep(10)
        if config.has_changed():
            config.reload()
            fraud_engine.__init__(config.rules)


async def expiry_checker():
    while True:
        await asyncio.sleep(60)
        expired = assembler.check_expired()
        for txn in expired:
            txn.risk_level = "Medium"
            txn.triggered_rules = ["abandoned_transaction"]
            storage.append("transactions", txn.model_dump())
            alert_data = {
                "id": txn.id,
                "store_id": txn.store_id,
                "risk_level": "Medium",
                "triggered_rules": ["abandoned_transaction"],
            }
            storage.append("alerts", alert_data)
            await ws_manager.broadcast("NEW_ALERT", alert_data)


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "buffer").mkdir(exist_ok=True)
    (DATA_DIR / "snippets").mkdir(exist_ok=True)
    (DATA_DIR / "events").mkdir(exist_ok=True)

    tasks = [
        asyncio.create_task(config_watcher()),
        asyncio.create_task(expiry_checker()),
    ]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="RLCC POC", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.receiver import router as receiver_router
from backend.camera_api import router as camera_router

app.include_router(receiver_router)
app.include_router(camera_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@app.get("/api/transactions")
async def list_transactions():
    txns = storage.read("transactions")
    txns.reverse()  # newest first
    return txns


@app.get("/api/transactions/{txn_id}")
async def get_transaction(txn_id: str):
    for txn in storage.read("transactions"):
        if txn.get("id") == txn_id:
            return txn
    return {"error": "not found"}


@app.get("/api/alerts")
async def list_alerts():
    alerts = storage.read("alerts")
    alerts.reverse()
    return alerts


@app.post("/api/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str, status: str, remarks: str = ""):
    storage.update("alerts", alert_id, {"status": status, "remarks": remarks})
    await ws_manager.broadcast("ALERT_UPDATED", {"id": alert_id, "status": status})
    return {"ok": True}


@app.get("/api/config")
async def get_config():
    return config.rules


@app.post("/api/config")
async def update_config(new_config: dict):
    config.rules.update(new_config)
    config.save_rules()
    fraud_engine.__init__(config.rules)
    return {"ok": True}


@app.get("/api/stores")
async def list_stores():
    return [{"cin": s.cin, "name": s.name, "pos_system": s.pos_system} for s in config.stores]


@app.get("/api/cameras")
async def list_cameras():
    return [
        {
            "seller_window_id": c.seller_window_id,
            "store_id": c.store_id,
            "pos_terminal": c.pos_terminal,
            "camera_id": c.camera_id,
            "rtsp_url": c.rtsp_url,
            "multi_pos": c.multi_pos,
            "zones": {"pos_zones": [{"zone_id": z.zone_id, "seller_zone": z.seller_zone, "bill_zone": z.bill_zone} for z in c.pos_zones]},
        }
        for c in config.cameras
    ]


# Serve dashboard static build if exists
dashboard_build = POC_DIR / "dashboard" / "dist"
if dashboard_build.exists():
    app.mount("/", StaticFiles(directory=str(dashboard_build), html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8001, reload=True)
