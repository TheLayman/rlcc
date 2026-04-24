from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import backend.deps as deps
from backend.assembler import TransactionAssembler
from backend.config import Config, StoreEntry
from backend.correlator import correlate
from backend.cv_consumer import ActivityState, CVConsumer
from backend.fraud import FraudEngine
from backend.models import Alert, TransactionSession
from backend.persistence import (
    find_transaction_by_bill_number,
    load_alerts,
    load_transactions,
    parse_dt as parse_record_dt,
    save_alerts,
    save_transactions,
    sort_alerts,
    sort_transactions,
)
from backend.sales_poller import SalesPoller, map_bill_to_transaction
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
deps.fraud_engine = FraudEngine(deps.config.rules, camera_config=deps.config)
deps.ws_manager = ConnectionManager()
deps.cv_consumer = CVConsumer(redis_url=deps.settings.redis_url)
deps.video_manager = VideoManager(data_dir=DATA_DIR, retention_days=deps.settings.video_retention_days)
sales_poller = SalesPoller(
    api_url=deps.settings.external_sales_url,
    api_token=deps.settings.external_sales_header_token,
    config=deps.config,
)
sales_sync_lock = asyncio.Lock()
sales_sync_state = {
    "configured": sales_poller.configured,
    "last_run_at": None,
    "last_success_at": None,
    "last_error": "",
    "last_mode": "",
    "last_fetched_bills": 0,
    "last_new_transactions": 0,
    "last_new_alerts": 0,
}


def _parse_dt(value) -> datetime | None:
    return parse_record_dt(value)


def _serialize_alert(alert: Alert) -> dict:
    return serialize_alert(
        alert,
        deps.config,
        video_manager=deps.video_manager,
        video_buffer_minutes=deps.settings.video_buffer_minutes,
    )


def _load_transactions() -> list[TransactionSession]:
    return load_transactions()


def _load_alerts() -> list[Alert]:
    return load_alerts()


def _sort_transactions(transactions: list[TransactionSession]) -> list[TransactionSession]:
    return sort_transactions(transactions)


def _sort_alerts(alerts: list[Alert]) -> list[Alert]:
    return sort_alerts(alerts)


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


def _hydrate_transaction(txn: TransactionSession) -> TransactionSession:
    txn.store_name = txn.store_name or deps.config.get_store_name(txn.store_id)
    camera = deps.config.get_camera_by_terminal(txn.store_id, txn.pos_terminal_no)
    if camera:
        txn.display_pos_label = txn.display_pos_label or camera.display_pos_label
        txn.camera_id = txn.camera_id or camera.camera_id
        txn.device_id = txn.device_id or camera.xprotect_device_id
        txn.seller_window_id = txn.seller_window_id or camera.seller_window_key
    return txn


def _transaction_bounds(txn: TransactionSession) -> tuple[datetime | None, datetime | None]:
    start_ts = _parse_dt(txn.started_at)
    end_ts = txn.committed_at if isinstance(txn.committed_at, datetime) else _parse_dt(str(txn.committed_at or ""))
    return start_ts, end_ts


def _clip_path_exists(path_value: str) -> bool:
    if not path_value:
        return False
    if deps.video_manager:
        return deps.video_manager.clip_exists(path_value)
    path = Path(path_value)
    return path.exists() and path.is_file() and path.stat().st_size > 0


def _can_recover_clip_from_buffer(txn: TransactionSession) -> bool:
    _start_ts, end_ts = _transaction_bounds(txn)
    if not end_ts:
        return False
    buffer_floor = datetime.now(timezone.utc) - timedelta(minutes=deps.settings.video_buffer_minutes + 1)
    return end_ts >= buffer_floor


def _extract_transaction_clip(txn: TransactionSession) -> str:
    if not deps.video_manager or not txn.camera_id:
        return ""
    start_ts, end_ts = _transaction_bounds(txn)
    if not start_ts or not end_ts:
        return ""
    if not _can_recover_clip_from_buffer(txn):
        return ""
    return deps.video_manager.extract_clip(
        camera_id=txn.camera_id,
        clip_id=txn.id,
        start_ts=start_ts - timedelta(seconds=30),
        end_ts=end_ts + timedelta(seconds=30),
    )


def _repair_alert_media_for_transaction(txn: TransactionSession) -> bool:
    raw_alerts = deps.storage.read("alerts")
    changed = False

    for record in raw_alerts:
        if record.get("transaction_id") != txn.id:
            continue

        record_snippet = str(record.get("snippet_path") or "")
        updates = {
            "store_name": txn.store_name or record.get("store_name") or deps.config.get_store_name(txn.store_id),
            "pos_terminal_no": record.get("pos_terminal_no") or txn.pos_terminal_no,
            "display_pos_label": record.get("display_pos_label") or txn.display_pos_label or txn.pos_terminal_no,
            "camera_id": record.get("camera_id") or txn.camera_id,
            "device_id": record.get("device_id") or txn.device_id,
        }

        if txn.snippet_path and (not record_snippet or not _clip_path_exists(record_snippet)):
            updates["snippet_path"] = txn.snippet_path
        elif record_snippet and not _clip_path_exists(record_snippet):
            updates["snippet_path"] = ""

        if any(record.get(key) != value for key, value in updates.items()):
            record.update(updates)
            changed = True

    if changed:
        deps.storage.replace("alerts", raw_alerts)
    return changed


def _repair_transaction_media(txn: TransactionSession) -> tuple[TransactionSession, bool]:
    original = txn.model_dump()
    txn = _hydrate_transaction(txn)

    if txn.snippet_path and not _clip_path_exists(txn.snippet_path):
        txn.snippet_path = ""

    if not txn.snippet_path:
        repaired_clip = _extract_transaction_clip(txn)
        if repaired_clip:
            txn.snippet_path = repaired_clip

    tracked_fields = (
        "store_name",
        "display_pos_label",
        "camera_id",
        "device_id",
        "seller_window_id",
        "snippet_path",
    )
    updates = {
        field_name: getattr(txn, field_name)
        for field_name in tracked_fields
        if original.get(field_name) != getattr(txn, field_name)
    }

    alerts_changed = _repair_alert_media_for_transaction(txn)
    if updates:
        _save_transaction_updates(txn.id, updates)

    return txn, bool(updates or alerts_changed)


def _needs_transaction_repair(txn: TransactionSession) -> bool:
    if not txn.store_name or not txn.display_pos_label or not txn.camera_id:
        return True
    if txn.snippet_path and not _clip_path_exists(txn.snippet_path):
        return True
    return not txn.snippet_path and _can_recover_clip_from_buffer(txn)


def _latest_transaction_timestamp() -> datetime | None:
    latest: datetime | None = None
    for txn in _load_transactions():
        candidate = _parse_dt(txn.committed_at or txn.started_at or txn.last_event_at)
        if candidate is None:
            continue
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=timezone.utc)
        if latest is None or candidate > latest:
            latest = candidate
    return latest


async def _ingest_polled_bills(bills: list[dict], *, mode: str) -> dict:
    fetched_bills = len(bills)
    new_transactions = 0
    new_alerts = 0

    existing_transactions = _load_transactions()
    existing_bill_numbers = {txn.bill_number: txn for txn in existing_transactions if txn.bill_number}

    def _bill_sort_key(bill: dict) -> str:
        return f"{bill.get('billDate', '')}T{bill.get('billTime', '')}|{bill.get('billSyncTime', '')}|{bill.get('billNo', '')}"

    for bill in sorted(bills, key=_bill_sort_key):
        txn = map_bill_to_transaction(bill, deps.config)
        if not txn.bill_number:
            continue

        existing = existing_bill_numbers.get(txn.bill_number)
        if existing is not None:
            repaired, _ = _repair_transaction_media(existing)
            existing_bill_numbers[txn.bill_number] = repaired
            continue

        txn = correlate(txn, deps.cv_consumer, deps.config)
        txn.snippet_path = _extract_transaction_clip(txn)

        alerts = deps.fraud_engine.evaluate(txn)
        for alert in alerts:
            alert.store_name = alert.store_name or txn.store_name
            alert.pos_terminal_no = alert.pos_terminal_no or txn.pos_terminal_no
            alert.display_pos_label = alert.display_pos_label or txn.display_pos_label
            alert.camera_id = alert.camera_id or txn.camera_id
            alert.device_id = alert.device_id or txn.device_id
            alert.snippet_path = alert.snippet_path or txn.snippet_path

        deps.storage.append("transactions", txn.model_dump())
        existing_bill_numbers[txn.bill_number] = txn
        new_transactions += 1
        await deps.ws_manager.broadcast("NEW_TRANSACTION", serialize_transaction(txn, deps.config))

        for alert in alerts:
            deps.storage.append("alerts", alert.model_dump())
            new_alerts += 1
            await deps.ws_manager.broadcast("NEW_ALERT", _serialize_alert(alert))

    return {
        "mode": mode,
        "fetched_bills": fetched_bills,
        "new_transactions": new_transactions,
        "new_alerts": new_alerts,
    }


async def _run_sales_sync(*, days: int | None = None, mode: str = "recent") -> dict:
    if not sales_poller.configured:
        sales_sync_state.update(
            {
                "configured": False,
                "last_mode": mode,
                "last_error": "sales API not configured",
            }
        )
        return {
            "ok": False,
            "mode": mode,
            "configured": False,
            "message": "sales API not configured",
            "fetched_bills": 0,
            "new_transactions": 0,
            "new_alerts": 0,
        }

    async with sales_sync_lock:
        now = datetime.now(timezone.utc)
        sales_sync_state["configured"] = True
        sales_sync_state["last_run_at"] = now.isoformat()
        sales_sync_state["last_mode"] = mode
        sales_sync_state["last_error"] = ""

        try:
            if days is not None:
                latest = _latest_transaction_timestamp()
                history_floor = now - timedelta(days=days)
                lookback = timedelta(minutes=max(deps.settings.sales_reconciliation_lookback_minutes, 1))
                if latest and latest > history_floor:
                    bills = await sales_poller.fetch_between(max(history_floor, latest - lookback), now)
                else:
                    bills = await sales_poller.fetch_historical(days)
            else:
                lookback = timedelta(minutes=max(deps.settings.sales_reconciliation_lookback_minutes, 1))
                latest = _latest_transaction_timestamp()
                start = (latest - lookback) if latest else (now - lookback)
                bills = await sales_poller.fetch_between(start, now)

            result = await _ingest_polled_bills(bills, mode=mode)
            sales_sync_state.update(
                {
                    "last_success_at": datetime.now(timezone.utc).isoformat(),
                    "last_error": "",
                    "last_fetched_bills": result["fetched_bills"],
                    "last_new_transactions": result["new_transactions"],
                    "last_new_alerts": result["new_alerts"],
                }
            )
            return {"ok": True, "configured": True, **result}
        except Exception as exc:
            sales_sync_state["last_error"] = str(exc)
            raise


async def config_watcher():
    while True:
        await asyncio.sleep(10)
        if deps.config.has_changed():
            deps.config.reload()
            deps.fraud_engine = FraudEngine(deps.config.rules, camera_config=deps.config)


async def expiry_checker():
    while True:
        await asyncio.sleep(30)
        expired = deps.assembler.check_expired()
        for txn in expired:
            txn.store_name = deps.config.get_store_name(txn.store_id)
            txn.risk_level = "Medium"
            txn.triggered_rules = ["abandoned_transaction"]
            txn.notes = "Transaction expired before CommitTransaction."

            if not txn.camera_id and txn.pos_terminal_no:
                camera = deps.config.get_camera_by_terminal(txn.store_id, txn.pos_terminal_no)
                if camera:
                    txn.camera_id = camera.camera_id
                    txn.display_pos_label = txn.display_pos_label or camera.display_pos_label

            snippet = ""
            anchor = _parse_dt(txn.last_event_at) or _parse_dt(txn.started_at)
            if deps.video_manager and txn.camera_id and anchor:
                try:
                    snippet = deps.video_manager.extract_clip(
                        camera_id=txn.camera_id,
                        clip_id=f"abandoned-{txn.id}",
                        start_ts=anchor - timedelta(seconds=30),
                        end_ts=anchor + timedelta(seconds=30),
                    )
                except Exception as exc:
                    print(f"[expiry] clip extraction failed for {txn.id}: {exc}")
            txn.snippet_path = snippet

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
                snippet_path=snippet,
                source="expired_transaction",
            )
            deps.storage.append("alerts", alert.model_dump())
            await deps.ws_manager.broadcast("NEW_ALERT", _serialize_alert(alert))


async def debug_broadcaster():
    while True:
        await asyncio.sleep(3)
        await deps.ws_manager.broadcast("RAW_VAS_DATA", deps.cv_consumer.get_recent_signals())
        await deps.ws_manager.broadcast("RAW_POS_DATA", deps.storage.get_recent_pos_events())
        await deps.ws_manager.broadcast("CV_ACTIVITY", deps.cv_consumer.get_recent_activity())


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
            await deps.ws_manager.broadcast("NEW_ALERT", _serialize_alert(alert))


async def snippet_cleanup():
    while True:
        await asyncio.sleep(3600)
        deps.video_manager.cleanup_old_snippets()


async def sales_reconciliation_loop():
    await asyncio.sleep(5)
    while True:
        try:
            await _run_sales_sync(mode="recent")
        except Exception as exc:
            print(f"[sales-sync] {exc}")
        await asyncio.sleep(max(deps.settings.sales_reconciliation_minutes, 1) * 60)


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
        asyncio.create_task(sales_reconciliation_loop()),
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
    if sales_poller.configured and sales_sync_state.get("last_error"):
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
        "sales": {
            "configured": sales_poller.configured,
            "api_url": deps.settings.external_sales_url,
            "reconciliation_minutes": deps.settings.sales_reconciliation_minutes,
            "lookback_minutes": deps.settings.sales_reconciliation_lookback_minutes,
            "last_run_at": sales_sync_state.get("last_run_at"),
            "last_success_at": sales_sync_state.get("last_success_at"),
            "last_error": sales_sync_state.get("last_error"),
            "last_mode": sales_sync_state.get("last_mode"),
            "last_fetched_bills": sales_sync_state.get("last_fetched_bills"),
            "last_new_transactions": sales_sync_state.get("last_new_transactions"),
            "last_new_alerts": sales_sync_state.get("last_new_alerts"),
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
    transactions = [
        _repair_transaction_media(txn)[0] if _needs_transaction_repair(txn) else txn
        for txn in transactions
    ]
    serialized = [serialize_transaction(txn, deps.config) for txn in transactions]
    bills_map = {txn.id: build_bill_data(txn) for txn in transactions}
    return {"transactions": serialized, "bills_map": bills_map, "count": len(serialized)}


@app.get("/api/transactions/{txn_id}")
async def get_transaction(txn_id: str):
    txn = _find_transaction(txn_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    txn, _ = _repair_transaction_media(txn)
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


_RANGE_CHUNK_SIZE = 1024 * 1024


def _video_headers(download_name: str) -> dict[str, str]:
    return {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="{download_name}"',
    }


def _range_video_response(file_path: str, request: Request, download_name: str) -> Response:
    path = Path(file_path)
    file_size = path.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")

    if not range_header or not range_header.startswith("bytes="):
        return FileResponse(
            file_path,
            media_type="video/mp4",
            headers=_video_headers(download_name),
            content_disposition_type="inline",
        )

    try:
        raw = range_header.split("=", 1)[1].split(",", 1)[0].strip()
        start_s, _, end_s = raw.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
    except ValueError:
        raise HTTPException(status_code=416, detail="Invalid Range header")

    if start >= file_size or end >= file_size or start > end:
        raise HTTPException(
            status_code=416,
            detail="Requested Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    length = end - start + 1

    def iter_chunks():
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(_RANGE_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        **_video_headers(download_name),
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
    }
    return StreamingResponse(iter_chunks(), status_code=206, media_type="video/mp4", headers=headers)


@app.get("/api/transactions/{txn_id}/video")
async def get_transaction_video(txn_id: str, request: Request):
    txn = _find_transaction(txn_id)
    if txn:
        txn, _ = _repair_transaction_media(txn)
    if not txn or not txn.snippet_path or not _clip_path_exists(txn.snippet_path):
        raise HTTPException(status_code=404, detail="Transaction clip not found")
    return _range_video_response(txn.snippet_path, request, f"{txn_id}.mp4")


@app.get("/api/alerts")
async def list_alerts():
    alerts = _sort_alerts(_load_alerts())
    return [_serialize_alert(alert) for alert in alerts]


@app.get("/api/alerts/{alert_id}/video")
async def get_alert_video(alert_id: str, request: Request):
    alert = _find_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert clip not found")

    clip_path = alert.snippet_path
    download_name = f"{alert_id}.mp4"

    if (not clip_path or not _clip_path_exists(clip_path)) and alert.transaction_id:
        txn = _find_transaction(alert.transaction_id)
        if txn:
            txn, _ = _repair_transaction_media(txn)
        if txn and txn.snippet_path and _clip_path_exists(txn.snippet_path):
            clip_path = txn.snippet_path
            download_name = f"{txn.id}.mp4"

    if not clip_path or not _clip_path_exists(clip_path):
        raise HTTPException(status_code=404, detail="Alert clip not found")

    return _range_video_response(clip_path, request, download_name)


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
    deps.fraud_engine = FraudEngine(deps.config.rules, camera_config=deps.config)
    return {"ok": True, "config": merged}


@app.get("/api/stores")
async def list_stores():
    return [
        {"cin": store.cin, "name": store.name, "pos_system": store.pos_system, "operator": store.operator}
        for store in deps.config.stores
    ]


@app.post("/api/stores")
async def update_stores(payload: dict):
    stores = payload.get("stores")
    if not isinstance(stores, list):
        return {"ok": False, "message": "Expected `stores` list"}

    normalized: list[StoreEntry] = []
    seen_cins: set[str] = set()
    for index, raw in enumerate(stores, start=1):
        if not isinstance(raw, dict):
            return {"ok": False, "message": f"Invalid store entry at index {index}"}

        cin = str(raw.get("cin") or "").strip()
        name = str(raw.get("name") or "").strip()
        pos_system = str(raw.get("pos_system") or "").strip() or "Posifly-Dino"
        operator = str(raw.get("operator") or "").strip()

        if not cin:
            return {"ok": False, "message": f"Store entry {index} is missing `cin`"}
        if not name:
            return {"ok": False, "message": f"Store entry {index} is missing `name`"}
        if cin in seen_cins:
            return {"ok": False, "message": f"Duplicate store ID `{cin}`"}

        seen_cins.add(cin)
        normalized.append(
            StoreEntry(
                cin=cin,
                name=name,
                pos_system=pos_system,
                operator=operator,
            )
        )

    deps.config.stores = normalized
    deps.config.save_stores()
    deps.config.reload()
    return {
        "ok": True,
        "stores": [
            {"cin": store.cin, "name": store.name, "pos_system": store.pos_system, "operator": store.operator}
            for store in deps.config.stores
        ],
    }


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
    try:
        result = await _run_sales_sync(days=days, mode="history")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    transactions = _sort_transactions(_load_transactions())
    serialized = [serialize_transaction(txn, deps.config) for txn in transactions]
    bills_map = {txn.id: build_bill_data(txn) for txn in transactions}
    return {
        **result,
        "days": days,
        "count": len(serialized),
        "transactions": serialized,
        "bills_map": bills_map,
    }


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
