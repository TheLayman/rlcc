import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/v1/rlcc/launch-event")
async def receive_event(request: Request):
    from backend.main import assembler, storage, fraud_engine, ws_manager, config
    from backend.models import Alert

    # Parse body — handle both stringified and normal JSON
    try:
        body = await request.body()
        body_str = body.decode("utf-8")
        try:
            parsed = json.loads(body_str)
            if isinstance(parsed, str):
                payload = json.loads(parsed)
            else:
                payload = parsed
        except (json.JSONDecodeError, TypeError):
            return JSONResponse(status_code=400, content={"message": "Invalid JSON"})
    except Exception:
        return JSONResponse(status_code=400, content={"message": "Invalid request body"})

    event_type = payload.get("event", "")

    # WAL — persist raw event immediately
    storage.append_event(payload)

    # Dedup
    if storage.is_duplicate(payload):
        return {"status": 200, "message": "duplicate, ignored"}
    storage.mark_seen(payload)

    # Track Nukkad activity for feed-down detection
    store_id = payload.get("storeIdentifier", "")
    if store_id:
        fraud_engine.record_nukkad_event(store_id)

    # Route by event type
    if event_type == "BeginTransactionWithTillLookup":
        assembler.begin(payload)

    elif event_type in ("AddTransactionSaleLine", "AddTransactionSaleLineWithTillLookup"):
        assembler.add_sale_line(payload)

    elif event_type == "AddTransactionPaymentLine":
        assembler.add_payment_line(payload)

    elif event_type == "AddTransactionTotalLine":
        assembler.add_total_line(payload)

    elif event_type == "AddTransactionEvent":
        assembler.add_event(payload)

    elif event_type == "CommitTransaction":
        txn = assembler.commit(payload)
        if txn:
            from backend.correlator import correlate
            from backend.main import cv_consumer
            txn = correlate(txn, cv_consumer, config)
            # Run fraud rules
            alerts = fraud_engine.evaluate(txn)

            # Persist transaction
            storage.append("transactions", txn.model_dump())
            await ws_manager.broadcast("NEW_TRANSACTION", {
                "id": txn.id, "store_id": txn.store_id,
                "risk_level": txn.risk_level, "triggered_rules": txn.triggered_rules
            })

            # Persist and broadcast alerts
            for alert in alerts:
                storage.append("alerts", alert.model_dump())
                await ws_manager.broadcast("NEW_ALERT", alert.model_dump())

    elif event_type == "BillReprint":
        alert = Alert(
            transaction_id="",
            store_id=payload.get("storeIdentifier", ""),
            pos_zone=payload.get("posTerminalNo", ""),
            cashier_id=payload.get("cashier", ""),
            risk_level="Medium",
            triggered_rules=["13_bill_reprint"],
        )
        storage.append("alerts", alert.model_dump())
        await ws_manager.broadcast("NEW_ALERT", alert.model_dump())

    return {"status": 200, "message": "Success"}
