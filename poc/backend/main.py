from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import backend.deps as deps
from backend.assembler import TransactionAssembler
from backend.config import Config
from backend.cv_consumer import ActivityState, CVConsumer
from backend.fraud import FraudEngine
from backend.models import Alert, TransactionSession
from backend.serializers import build_bill_data, serialize_alert, serialize_transaction
from backend.settings import get_settings
from backend.storage import Storage
from backend.timeline import build_timeline
from backend.video import VideoManager
from backend.ws import ConnectionManager

POC_DIR = Path(__file__).parent.parent
CONFIG_DIR = POC_DIR / "config"
DATA_DIR = POC_DIR / "data"
LOGS_DIR = POC_DIR / "logs"

deps.settings = get_settings(POC_DIR / ".env")
deps.config = Config(config_dir=str(CONFIG_DIR))
deps.storage = Storage(data_dir=str(DATA_DIR))
deps.assembler = TransactionAssembler()
deps.fraud_engine = FraudEngine(deps.config.rules)
deps.ws_manager = ConnectionManager()
deps.cv_consumer = CVConsumer(redis_url=deps.settings.redis_url)
deps.video_manager = VideoManager(data_dir=DATA_DIR, retention_days=deps.settings.video_retention_days)


def _parse_dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_transactions() -> list[TransactionSession]:
    txns: list[TransactionSession] = []
    for record in deps.storage.read("transactions"):
        try:
            txns.append(TransactionSession(**record))
        except Exception:
            continue
    return txns


def _load_alerts() -> list[Alert]:
    alerts: list[Alert] = []
    for record in deps.storage.read("alerts"):
        try:
            alerts.append(Alert(**record))
        except Exception:
            continue
    return alerts


def _sort_transactions(transactions: list[TransactionSession]) -> list[TransactionSession]:
    return sorted(
        transactions,
        key=lambda txn: _parse_dt(txn.committed_at or txn.started_at or txn.last_event_at) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def _sort_alerts(alerts: list[Alert]) -> list[Alert]:
    return sorted(alerts, key=lambda alert: _parse_dt(alert.timestamp) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def _find_transaction(txn_id: str) -> TransactionSession | None:
    for txn in _load_transactions():
        if txn.id == txn_id:
            return txn
    return None


def _find_alert(alert_id: str) -> Alert | None:
    for alert in _load_alerts():
        if alert.id == alert_id:
            return alert
    return None


def _save_transaction_updates(txn_id: str, updates: dict) -> None:
    deps.storage.update("transactions", txn_id, updates)


def _save_alert_updates(alert_id: str, updates: dict) -> None:
    deps.storage.update("alerts", alert_id, updates)


def _update_alerts_for_transaction(transaction_id: str, updates: dict) -> list[Alert]:
    changed: list[Alert] = []
    raw_alerts = deps.storage.read("alerts")
    for record in raw_alerts:
        if record.get("transaction_id") == transaction_id:
            record.update(updates)
            try:
                changed.append(Alert(**record))
            except Exception:
                continue
    deps.storage.replace("alerts", raw_alerts)
    return changed


def _missing_pos_seconds() -> int:
    return int(deps.config.rules.get("missing_pos_seconds", 30))


async def config_watcher():
    while True:
        await asyncio.sleep(10)
        if deps.config.has_changed():
            deps.config.reload()
            deps.fraud_engine = FraudEngine(deps.config.rules)


async def expiry_checker():
    while True:
        await asyncio.sleep(30)
        expired = deps.assembler.check_expired()
        for txn in expired:
            txn.store_name = deps.config.get_store_name(txn.store_id)
            txn.risk_level = "Medium"
            txn.triggered_rules = ["abandoned_transaction"]
            txn.notes = "Transaction expired before CommitTransaction."
            deps.storage.append("transactions", txn.model_dump())
            alert = Alert(
                transaction_id=txn.id,
                store_id=txn.store_id,
                store_name=txn.store_name,
                pos_terminal_no=txn.pos_terminal_no,
                display_pos_label=txn.display_pos_label or txn.pos_terminal_no,
                cashier_id=txn.cashier_id,
                risk_level="Medium",
                triggered_rules=["abandoned_transaction"],
                camera_id=txn.camera_id,
                device_id=txn.device_id,
                source="expired_transaction",
            )
            deps.storage.append("alerts", alert.model_dump())
            await deps.ws_manager.broadcast("NEW_ALERT", serialize_alert(alert, deps.config))


async def debug_broadcaster():
    while True:
        await asyncio.sleep(3)
        await deps.ws_manager.broadcast("RAW_VAS_DATA", deps.cv_consumer.get_recent_signals())
        await deps.ws_manager.broadcast("RAW_POS_DATA", deps.storage.get_recent_pos_events())


def _build_missing_pos_alert(state: ActivityState, now: datetime) -> Alert | None:
    mapping = deps.config.get_zone_entry(state.camera_id, state.pos_zone)
    if not mapping:
        return None
    camera, _zone = mapping
    snippet = ""
    if deps.video_manager:
        snippet = deps.video_manager.extract_clip(
            camera_id=state.camera_id,
            clip_id=f"missing-pos-{state.camera_id}-{int(now.timestamp())}",
            start_ts=state.started_at - timedelta(seconds=30),
            end_ts=state.last_seen + timedelta(seconds=30),
        )
    return Alert(
        transaction_id="",
        store_id=camera.store_id,
        store_name=deps.config.get_store_name(camera.store_id),
        pos_terminal_no=camera.pos_terminal_no,
        display_pos_label=camera.display_pos_label,
        risk_level="High",
        triggered_rules=["24_missing_pos"],
        timestamp=state.last_seen,
        camera_id=state.camera_id,
        cv_window_start=state.started_at,
        cv_window_end=state.last_seen,
        snippet_path=snippet,
        source="cv_missing_pos",
    )


async def missing_pos_checker():
    while True:
        await asyncio.sleep(5)
        now = datetime.now(timezone.utc)
        deps.cv_consumer.prune_inactive_states(stale_after_seconds=20)

        if not deps.fraud_engine._enabled("24_missing_pos"):
            continue

        for state in list(deps.cv_consumer.activity_states.values()):
            if not state.active or state.alert_emitted:
                continue

            mapping = deps.config.get_zone_entry(state.camera_id, state.pos_zone)
            if not mapping:
                continue
            camera, _zone = mapping

            if deps.fraud_engine.is_feed_down(camera.store_id):
                continue
            if deps.assembler.has_open_session(camera.store_id, camera.pos_terminal_no):
                continue

            dwell_seconds = (now - state.started_at).total_seconds()
            if dwell_seconds < _missing_pos_seconds():
                continue

            alert = _build_missing_pos_alert(state, now)
            if not alert:
                continue

            state.alert_emitted = True
            deps.storage.append("alerts", alert.model_dump())
            await deps.ws_manager.broadcast("NEW_ALERT", serialize_alert(alert, deps.config))


async def snippet_cleanup():
    while True:
        await asyncio.sleep(3600)
        deps.video_manager.cleanup_old_snippets()


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "buffer").mkdir(exist_ok=True)
    (DATA_DIR / "snippets").mkdir(exist_ok=True)
    (DATA_DIR / "events").mkdir(exist_ok=True)
    (DATA_DIR / "redis").mkdir(exist_ok=True)

    tasks = [
        asyncio.create_task(config_watcher()),
        asyncio.create_task(expiry_checker()),
        asyncio.create_task(debug_broadcaster()),
        asyncio.create_task(missing_pos_checker()),
        asyncio.create_task(snippet_cleanup()),
        asyncio.create_task(deps.cv_consumer.run()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="RLCC POC", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.camera_api import router as camera_router
from backend.receiver import router as receiver_router

app.include_router(receiver_router)
app.include_router(camera_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await deps.ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        deps.ws_manager.disconnect(websocket)


@app.get("/health")
async def health():
    disk_usage = shutil.disk_usage(DATA_DIR)
    mapping_issues = deps.config.validate_mappings()
    recent_pos = deps.storage.get_recent_pos_events()
    last_push_event = recent_pos[0] if recent_pos else {}
    last_push_ts = (
        last_push_event.get("transactionTimeStamp")
        or last_push_event.get("lineTimeStamp")
        or last_push_event.get("ts")
        or None
    )
    cv_health = deps.cv_consumer.get_health()
    status = "ok"
    if mapping_issues or not cv_health.get("connected"):
        status = "degraded"

    return {
        "status": status,
        "backend": {
            "host": deps.settings.backend_host,
            "port": deps.settings.backend_port,
            "push_endpoint": f"http://<server-ip>:{deps.settings.backend_port}/v1/rlcc/launch-event",
            "push_auth_header": "x-authorization-key",
            "last_push_event_at": last_push_ts,
            "recent_pos_events": len(recent_pos),
        },
        "cv": cv_health,
        "config": {
            "store_count": len(deps.config.stores),
            "camera_count": len(deps.config.cameras),
            "mapping_issues": mapping_issues,
        },
        "storage": {
            "events_dir": str(DATA_DIR / "events"),
            "snippets_dir": str(DATA_DIR / "snippets"),
            "buffer_dir": str(DATA_DIR / "buffer"),
            "free_gb": round(disk_usage.free / (1024 ** 3), 2),
        },
        "services": {
            "redis_url": deps.settings.redis_url,
            "dashboard_port": deps.settings.dashboard_port,
            "cv_port": deps.settings.cv_port,
        },
    }


@app.get("/api/transactions")
async def list_transactions():
    transactions = _sort_transactions(_load_transactions())
    serialized = [serialize_transaction(txn, deps.config) for txn in transactions]
    bills_map = {txn.id: build_bill_data(txn) for txn in transactions}
    return {"transactions": serialized, "bills_map": bills_map, "count": len(serialized)}


@app.get("/api/transactions/{txn_id}")
async def get_transaction(txn_id: str):
    txn = _find_transaction(txn_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {
        "transaction": serialize_transaction(txn, deps.config),
        "bill_data": build_bill_data(txn),
        "timeline": build_timeline(txn),
    }


@app.get("/api/transactions/{txn_id}/timeline")
async def get_timeline(txn_id: str):
    txn = _find_transaction(txn_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return build_timeline(txn)


@app.get("/api/transactions/{txn_id}/video")
async def get_transaction_video(txn_id: str):
    txn = _find_transaction(txn_id)
    if not txn or not txn.snippet_path or not Path(txn.snippet_path).exists():
        raise HTTPException(status_code=404, detail="Transaction clip not found")
    return FileResponse(txn.snippet_path, media_type="video/mp4", filename=f"{txn_id}.mp4")


@app.get("/api/alerts")
async def list_alerts():
    alerts = _sort_alerts(_load_alerts())
    return [serialize_alert(alert, deps.config) for alert in alerts]


@app.get("/api/alerts/{alert_id}/video")
async def get_alert_video(alert_id: str):
    alert = _find_alert(alert_id)
    if not alert or not alert.snippet_path or not Path(alert.snippet_path).exists():
        raise HTTPException(status_code=404, detail="Alert clip not found")
    return FileResponse(alert.snippet_path, media_type="video/mp4", filename=f"{alert_id}.mp4")


@app.post("/api/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str, status: str = Query(...), remarks: str = Query(default="")):
    alert = _find_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    _save_alert_updates(
        alert_id,
        {
            "status": status,
            "remarks": remarks,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    await deps.ws_manager.broadcast("ALERT_UPDATED", {"id": alert_id, "status": status, "remarks": remarks})
    return {"ok": True}


@app.post("/api/admin/validate")
async def admin_validate(transaction_id: str, decision: str, notes: str = ""):
    transaction_updates = {"status": decision.lower(), "notes": notes}
    _save_transaction_updates(transaction_id, transaction_updates)
    changed_alerts = _update_alerts_for_transaction(transaction_id, {"status": decision, "remarks": notes})
    await deps.ws_manager.broadcast(
        "TRANSACTION_UPDATE",
        {"id": transaction_id, "status": decision.lower(), "notes": notes},
    )
    for alert in changed_alerts:
        await deps.ws_manager.broadcast("ALERT_UPDATED", {"id": alert.id, "status": alert.status, "remarks": alert.remarks})
    return {"ok": True}


@app.get("/api/config")
async def get_config():
    return deps.config.rules


@app.post("/api/config")
async def update_config(new_config: dict):
    merged = dict(deps.config.rules)
    for key, value in new_config.items():
        if key == "rules" and isinstance(value, dict):
            merged.setdefault("rules", {}).update(value)
        else:
            merged[key] = value
    deps.config.rules = merged
    deps.config.save_rules()
    deps.fraud_engine = FraudEngine(deps.config.rules)
    return {"ok": True, "config": merged}


@app.get("/api/stores")
async def list_stores():
    return [
        {"cin": store.cin, "name": store.name, "pos_system": store.pos_system, "operator": store.operator}
        for store in deps.config.stores
    ]


@app.get("/api/cameras")
async def list_cameras():
    issues = deps.config.validate_mappings()
    return {
        "issues": issues,
        "cameras": [
            {
                "seller_window_id": camera.seller_window_id,
                "store_id": camera.store_id,
                "pos_terminal_no": camera.pos_terminal_no,
                "display_pos_label": camera.display_pos_label,
                "camera_id": camera.camera_id,
                "rtsp_url": camera.rtsp_url,
                "multi_pos": camera.multi_pos,
                "xprotect_device_id": camera.xprotect_device_id,
                "enabled": camera.enabled,
                "zones": {
                    "pos_zones": [
                        {
                            "zone_id": zone.zone_id,
                            "seller_zone": zone.seller_zone,
                            "bill_zone": zone.bill_zone,
                        }
                        for zone in camera.pos_zones
                    ]
                },
            }
            for camera in deps.config.cameras
        ],
    }


@app.get("/api/history")
async def history(days: int = Query(default=5, ge=1, le=30)):
    mode = "noop"
    if deps.settings.external_sales_url and deps.settings.external_sales_header_token:
        mode = "reconciliation_pending"
    return {"ok": True, "days": days, "mode": mode}


@app.get("/api/reports/employee-scorecard")
async def employee_scorecard():
    report: dict[str, dict] = {}
    for txn in _load_transactions():
        cashier_id = txn.cashier_id or "unknown"
        bucket = report.setdefault(
            cashier_id,
            {
                "cashier_id": cashier_id,
                "transaction_count": 0,
                "manual_entry_count": 0,
                "manual_discount_count": 0,
                "void_count": 0,
                "flagged_count": 0,
                "total_value": 0.0,
            },
        )
        bucket["transaction_count"] += 1
        bucket["total_value"] += sum(item.total_amount for item in txn.items)
        triggered = set(txn.triggered_rules)
        if txn.risk_level != "Low":
            bucket["flagged_count"] += 1
        if "8_manual_entry" in triggered:
            bucket["manual_entry_count"] += 1
        if "10_manual_discount" in triggered:
            bucket["manual_discount_count"] += 1
        if "19_void_percentage" in triggered or "4_void_cancelled" in triggered:
            bucket["void_count"] += 1

    results = []
    for row in report.values():
        total = max(row["transaction_count"], 1)
        results.append(
            {
                **row,
                "manual_entry_rate": round((row["manual_entry_count"] / total) * 100, 2),
                "manual_discount_rate": round((row["manual_discount_count"] / total) * 100, 2),
                "void_rate": round((row["void_count"] / total) * 100, 2),
                "flagged_rate": round((row["flagged_count"] / total) * 100, 2),
            }
        )
    return sorted(results, key=lambda row: row["flagged_rate"], reverse=True)


dashboard_build = POC_DIR / "dashboard" / "dist"
if dashboard_build.exists():
    app.mount("/", StaticFiles(directory=str(dashboard_build), html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=deps.settings.backend_host,
        port=deps.settings.backend_port,
        reload=True,
    )
