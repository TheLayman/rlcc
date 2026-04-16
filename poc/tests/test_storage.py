import json
from backend.storage import Storage


def test_append_and_read(tmp_path):
    s = Storage(data_dir=str(tmp_path))
    s.append("transactions", {"id": "TXN-001", "amount": 100})
    s.append("transactions", {"id": "TXN-002", "amount": 200})
    records = s.read("transactions")
    assert len(records) == 2
    assert records[0]["id"] == "TXN-001"


def test_append_event_wal(tmp_path):
    s = Storage(data_dir=str(tmp_path))
    s.append_event({"event": "BeginTransactionWithTillLookup", "transactionSessionId": "s1"})
    events = s.read_events()
    assert len(events) == 1


def test_dedup(tmp_path):
    s = Storage(data_dir=str(tmp_path))
    event = {"transactionSessionId": "s1", "event": "AddTransactionSaleLine", "lineNumber": 1}
    assert not s.is_duplicate(event)
    s.mark_seen(event)
    assert s.is_duplicate(event)


def test_update_record(tmp_path):
    s = Storage(data_dir=str(tmp_path))
    s.append("alerts", {"id": "ALT-001", "status": "new"})
    s.update("alerts", "ALT-001", {"status": "resolved", "remarks": "genuine"})
    records = s.read("alerts")
    assert records[0]["status"] == "resolved"
    assert records[0]["remarks"] == "genuine"
