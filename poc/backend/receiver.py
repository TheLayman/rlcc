import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import backend.deps as deps
from backend.models import Alert

router = APIRouter()


@router.post("/v1/rlcc/launch-event")
async def receive_event(request: Request):

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
    deps.storage.append_event(payload)

    # Dedup
    if deps.storage.is_duplicate(payload):
        return {"status": 200, "message": "duplicate, ignored"}
    deps.storage.mark_seen(payload)

    # Track Nukkad activity for feed-down detection
    store_id = payload.get("storeIdentifier", "")
    if store_id:
        deps.fraud_engine.record_nukkad_event(store_id)

    # Route by event type
    if event_type == "BeginTransactionWithTillLookup":
        deps.assembler.begin(payload)

    elif event_type in ("AddTransactionSaleLine", "AddTransactionSaleLineWithTillLookup"):
        deps.assembler.add_sale_line(payload)

    elif event_type == "AddTransactionPaymentLine":
        deps.assembler.add_payment_line(payload)

    elif event_type == "AddTransactionTotalLine":
        deps.assembler.add_total_line(payload)

    elif event_type == "AddTransactionEvent":
        deps.assembler.add_event(payload)

    elif event_type == "CommitTransaction":
        txn = deps.assembler.commit(payload)
        if txn:
            from backend.correlator import correlate
            txn = correlate(txn, deps.cv_consumer, deps.config)
            # Run fraud rules
            alerts = deps.fraud_engine.evaluate(txn)

            # Persist transaction
            deps.storage.append("transactions", txn.model_dump())
            await deps.ws_manager.broadcast("NEW_TRANSACTION", {
                "id": txn.id, "store_id": txn.store_id,
                "risk_level": txn.risk_level, "triggered_rules": txn.triggered_rules
            })

            # Persist and broadcast alerts
            for alert in alerts:
                deps.storage.append("alerts", alert.model_dump())
                await deps.ws_manager.broadcast("NEW_ALERT", alert.model_dump())

    elif event_type == "BillReprint":
        alert = Alert(
            transaction_id="",
            store_id=payload.get("storeIdentifier", ""),
            pos_zone=payload.get("posTerminalNo", ""),
            cashier_id=payload.get("cashier", ""),
            risk_level="Medium",
            triggered_rules=["13_bill_reprint"],
        )
        deps.storage.append("alerts", alert.model_dump())
        await deps.ws_manager.broadcast("NEW_ALERT", alert.model_dump())

    return {"status": 200, "message": "Success"}
