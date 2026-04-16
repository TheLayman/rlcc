# POC Backend + Emulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the POC backend (FastAPI) and Nukkad event emulator — the foundation that CV service and dashboard depend on.

**Architecture:** FastAPI app receives Nukkad push events (stringified JSON), assembles into transactions, consumes CV signals from Redis, correlates both streams, runs 29 fraud rules, broadcasts alerts via WebSocket, serves REST API. Emulator generates realistic POS events + CV signals for testing without live connections.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, Redis (pub/sub), JSONL storage, httpx, uvicorn

**Depends on:** Redis running on localhost:6379

**Produces:** Working backend on :8001 + emulator that generates end-to-end test data

---

## File Map

```
poc/
├── backend/
│   ├── __init__.py
│   ├── main.py           — FastAPI app, lifespan, WebSocket, static mount
│   ├── models.py          — all Pydantic models
│   ├── storage.py         — JSONL append/read/rotate, dedup, WAL
│   ├── config.py          — load stores, camera mapping, rules; hot reload
│   ├── receiver.py        — POST /v1/rlcc/launch-event (stringified JSON)
│   ├── assembler.py       — transaction state machine (OPEN/COMMITTED/EXPIRED)
│   ├── cv_consumer.py     — Redis subscriber, 30s window aggregation
│   ├── correlator.py      — match txns to CV windows
│   ├── fraud.py           — 29 rules + risk matrix + feed-down
│   ├── timeline.py        — merge POS + CV events
│   ├── snippets.py        — extract video from rolling buffer
│   ├── camera_api.py      — store/camera/zone CRUD + frame grab
│   ├── reconciler.py      — sales API poll + gap backfill
│   └── ws.py              — WebSocket connection manager
├── emulator/
│   ├── __init__.py
│   ├── nukkad_emulator.py — push event generator
│   ├── cv_emulator.py     — Redis CV signal generator
│   └── scenarios.py       — fraud scenario definitions
├── config/
│   ├── stores.json
│   ├── camera_mapping.json
│   └── rule_config.json
├── data/                   — created at runtime
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_storage.py
│   ├── test_assembler.py
│   ├── test_fraud.py
│   ├── test_correlator.py
│   ├── test_receiver.py
│   └── test_emulator.py
├── requirements.txt
├── pytest.ini
└── README.md
```

---

### Task 1: Project scaffold + dependencies

**Files:**
- Create: `poc/requirements.txt`
- Create: `poc/pytest.ini`
- Create: `poc/README.md`
- Create: `poc/backend/__init__.py`
- Create: `poc/emulator/__init__.py`
- Create: `poc/tests/__init__.py`
- Create: `poc/config/stores.json`
- Create: `poc/config/camera_mapping.json`
- Create: `poc/config/rule_config.json`

- [ ] **Step 1: Create poc directory structure**

```bash
cd /Users/gongura/Code/rlcc/rlcc-scope
mkdir -p poc/{backend,emulator,tests,config,data/{buffer,snippets,events}}
touch poc/backend/__init__.py poc/emulator/__init__.py poc/tests/__init__.py
```

- [ ] **Step 2: Create requirements.txt**

```
# poc/requirements.txt
fastapi>=0.128.0
uvicorn[standard]>=0.40.0
redis>=5.0.0
httpx>=0.28.0
pydantic>=2.0.0
python-dotenv>=1.0.0
numpy>=1.24.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

Note: `ultralytics` and `opencv-python-headless` are for cv_service (separate plan). Backend doesn't need them.

- [ ] **Step 3: Create pytest.ini**

```ini
# poc/pytest.ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

- [ ] **Step 4: Create config/stores.json**

```json
[
  {"cin": "NDCIN1223", "name": "Ram Ki Bandi", "pos_system": "Posifly-Dino"},
  {"cin": "NSCIN8227", "name": "Encalm Lounge", "pos_system": "Posifly-Dino"},
  {"cin": "NDCIN1227", "name": "KFC", "pos_system": "Posifly-Dino"},
  {"cin": "NDCIN1226", "name": "Pizza Hut", "pos_system": "Posifly-Dino"},
  {"cin": "NDCIN1228", "name": "Haldirams AeroPlaza", "pos_system": "Posifly-Dino"}
]
```

- [ ] **Step 5: Create config/camera_mapping.json**

```json
[
  {
    "seller_window_id": "NDCIN1223_POS3",
    "store_id": "NDCIN1223",
    "pos_terminal": "POS 3",
    "camera_id": "cam-rambandi-01",
    "rtsp_url": "",
    "xprotect_device_id": "",
    "multi_pos": false,
    "zones": {
      "pos_zones": [
        {
          "zone_id": "POS3",
          "seller_zone": [[431,568], [861,550], [872,720], [420,720]],
          "bill_zone": [[710,375], [850,370], [855,440], [715,445]]
        }
      ]
    }
  }
]
```

- [ ] **Step 6: Create config/rule_config.json**

```json
{
  "discount_threshold_percent": 20,
  "refund_amount_threshold": 0,
  "high_value_threshold": 2000,
  "bulk_quantity_threshold": 10,
  "idle_pos_minutes": 30,
  "void_percentage_threshold": 50,
  "missing_pos_seconds": 30,
  "feed_down_minutes": 10,
  "rules": {
    "1_high_discount": {"enabled": true},
    "2_refund_excess": {"enabled": true},
    "3_complementary": {"enabled": true},
    "4_void_cancelled": {"enabled": true},
    "5_negative_amount": {"enabled": true},
    "6_high_value": {"enabled": true},
    "7_bulk_purchase": {"enabled": true},
    "8_manual_entry": {"enabled": true},
    "9_manual_price": {"enabled": true},
    "10_manual_discount": {"enabled": true},
    "11_self_granted_discount": {"enabled": true},
    "12_drawer_opened": {"enabled": true},
    "13_bill_reprint": {"enabled": true},
    "14_null_transaction": {"enabled": true},
    "15_post_bill_cancel": {"enabled": true},
    "16_return_not_recent": {"enabled": true},
    "17_exchange_no_match": {"enabled": true},
    "18_employee_purchase": {"enabled": true},
    "19_void_percentage": {"enabled": true},
    "20_outside_hours": {"enabled": false},
    "21_credit_note": {"enabled": true},
    "22_manual_card": {"enabled": false},
    "23_full_return": {"enabled": true},
    "24_missing_pos": {"enabled": true},
    "25_pos_idle": {"enabled": true},
    "26_void_no_customer": {"enabled": true},
    "27_return_no_customer": {"enabled": true},
    "28_drawer_no_customer": {"enabled": true},
    "29_bill_not_generated": {"enabled": true}
  }
}
```

- [ ] **Step 7: Create README.md**

```markdown
# RLCC POC

## Setup

```bash
cd poc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
# Start Redis first
redis-server --daemonize yes --dir ./data

# Start backend
python -m backend.main

# In another terminal, run emulator
python -m emulator.nukkad_emulator
```

## Test

```bash
pytest -v
```
```

- [ ] **Step 8: Commit**

```bash
git add poc/
git commit -m "feat(poc): scaffold project structure + configs"
```

---

### Task 2: Pydantic models

**Files:**
- Create: `poc/backend/models.py`
- Create: `poc/tests/test_models.py`

- [ ] **Step 1: Write tests for models**

```python
# poc/tests/test_models.py
from backend.models import (
    SaleLine, PaymentLine, TotalLine, TransactionEvent,
    TransactionSession, Alert, CVWindow
)
from datetime import datetime, timezone


def test_sale_line_from_nukkad():
    payload = {
        "lineTimeStamp": "2026-04-16T10:02:05Z",
        "lineNumber": 1,
        "itemDescription": "Chicken Burger",
        "itemQuantity": 2,
        "itemUnitPrice": 249.0,
        "totalAmount": 498.0,
        "scanAttribute": "Auto",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
        "grantedBy": "",
    }
    line = SaleLine.from_nukkad(payload)
    assert line.item_description == "Chicken Burger"
    assert line.scan_attribute == "Auto"
    assert line.total_amount == 498.0


def test_payment_line_from_nukkad():
    payload = {
        "lineTimeStamp": "2026-04-16T10:02:35Z",
        "lineNumber": 1,
        "lineAttribute": "Cash",
        "paymentDescription": "Cash",
        "amount": 500.0,
        "cardType": "",
        "paymentTypeID": "",
    }
    line = PaymentLine.from_nukkad(payload)
    assert line.line_attribute == "Cash"
    assert line.amount == 500.0


def test_transaction_session_defaults():
    txn = TransactionSession(
        id="sess-001",
        store_id="NDCIN1223",
        pos_terminal="POS 3",
        source="push_assembled",
    )
    assert txn.status == "open"
    assert txn.items == []
    assert txn.payments == []
    assert txn.totals == []
    assert txn.events == []
    assert txn.risk_level == "Low"
    assert txn.triggered_rules == []


def test_alert_creation():
    alert = Alert(
        id="ALT-001",
        transaction_id="TXN-001",
        store_id="NDCIN1223",
        risk_level="High",
        triggered_rules=["5_negative_amount"],
    )
    assert alert.status == "new"
    assert alert.remarks is None


def test_cv_window_defaults():
    w = CVWindow(
        pos_zone="POS3",
        camera_id="cam-01",
        window_start=datetime.now(timezone.utc),
        window_end=datetime.now(timezone.utc),
    )
    assert w.seller_present_pct == 0.0
    assert w.non_seller_present_pct == 0.0
    assert w.bill_motion_detected is False
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd poc && python -m pytest tests/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.models'`

- [ ] **Step 3: Implement models.py**

```python
# poc/backend/models.py
from __future__ import annotations
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional
import uuid


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def gen_id(prefix: str = "ALT") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


class SaleLine(BaseModel):
    line_timestamp: Optional[str] = None
    line_number: int = 0
    item_id: str = ""
    item_description: str = ""
    item_quantity: int = 0
    item_unit_price: float = 0.0
    total_amount: float = 0.0
    scan_attribute: str = "None"  # None/Auto/ManuallyEntered/ModifiedUnitPrice
    item_attribute: str = "None"  # None/ReturnItem/CancellationWithinTransaction/...
    discount_type: str = "NoLineDiscount"
    discount: float = 0.0
    granted_by: str = ""

    @classmethod
    def from_nukkad(cls, p: dict) -> SaleLine:
        return cls(
            line_timestamp=p.get("lineTimeStamp"),
            line_number=p.get("lineNumber", 0),
            item_id=p.get("itemID", ""),
            item_description=p.get("itemDescription", ""),
            item_quantity=p.get("itemQuantity", 0),
            item_unit_price=p.get("itemUnitPrice", 0.0),
            total_amount=p.get("totalAmount", 0.0),
            scan_attribute=p.get("scanAttribute", "None"),
            item_attribute=p.get("itemAttribute", "None"),
            discount_type=p.get("discountType", "NoLineDiscount"),
            discount=p.get("discount", 0.0),
            granted_by=p.get("grantedBy", ""),
        )


class PaymentLine(BaseModel):
    line_timestamp: Optional[str] = None
    line_number: int = 0
    line_attribute: str = "None"  # Cash/CreditCard/UPI/GiftCard/CreditNotePayment/...
    payment_description: str = ""
    amount: float = 0.0
    card_type: str = ""
    payment_type_id: str = ""

    @classmethod
    def from_nukkad(cls, p: dict) -> PaymentLine:
        return cls(
            line_timestamp=p.get("lineTimeStamp"),
            line_number=p.get("lineNumber", 0),
            line_attribute=p.get("lineAttribute", "None"),
            payment_description=p.get("paymentDescription", ""),
            amount=p.get("amount", 0.0),
            card_type=p.get("cardType", ""),
            payment_type_id=p.get("paymentTypeID", ""),
        )


class TotalLine(BaseModel):
    line_attribute: str = ""  # SubTotal/VAT/TotalDiscount/TotalAmountToBePaid/...
    description: str = ""
    amount: float = 0.0

    @classmethod
    def from_nukkad(cls, p: dict) -> TotalLine:
        return cls(
            line_attribute=p.get("lineAttribute", ""),
            description=p.get("totalDescription", ""),
            amount=p.get("amount", 0.0),
        )


class TransactionEvent(BaseModel):
    line_timestamp: Optional[str] = None
    line_attribute: str = ""  # TransactionSuspended/TransactionResumed/TransactionCancelled
    event_description: str = ""

    @classmethod
    def from_nukkad(cls, p: dict) -> TransactionEvent:
        return cls(
            line_timestamp=p.get("lineTimeStamp"),
            line_attribute=p.get("lineAttribute", ""),
            event_description=p.get("eventDescription", ""),
        )


class TransactionSession(BaseModel):
    id: str
    store_id: str
    pos_terminal: str = ""
    cashier_id: str = ""
    transaction_type: str = "CompletedNormally"
    employee_purchase: bool = False
    outside_opening_hours: str = "InsideOpeningHours"
    source: str = "push_assembled"  # push_assembled/poll_reconciled/poll_primary_arms
    status: str = "open"  # open/committed/expired
    started_at: Optional[str] = None
    committed_at: Optional[datetime] = None
    bill_number: str = ""
    is_previous_transaction: bool = False
    linked_transaction_id: str = ""
    items: list[SaleLine] = Field(default_factory=list)
    payments: list[PaymentLine] = Field(default_factory=list)
    totals: list[TotalLine] = Field(default_factory=list)
    events: list[TransactionEvent] = Field(default_factory=list)
    risk_level: str = "Low"
    triggered_rules: list[str] = Field(default_factory=list)
    cv_non_seller_present: Optional[bool] = None
    cv_receipt_detected: Optional[bool] = None
    cv_non_seller_count: int = 0
    cv_confidence: str = ""  # HIGH/REDUCED/UNAVAILABLE/UNMAPPED
    camera_id: str = ""
    device_id: str = ""
    snippet_path: str = ""


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: gen_id("ALT"))
    transaction_id: str = ""
    store_id: str = ""
    pos_zone: str = ""
    cashier_id: str = ""
    risk_level: str = "Medium"
    triggered_rules: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)
    status: str = "new"  # new/reviewing/resolved/fraudulent/genuine
    resolved_by: str = ""
    resolved_at: Optional[datetime] = None
    remarks: Optional[str] = None
    camera_id: str = ""
    cv_window_start: Optional[datetime] = None
    cv_window_end: Optional[datetime] = None
    device_id: str = ""


class CVWindow(BaseModel):
    pos_zone: str
    camera_id: str
    window_start: datetime
    window_end: datetime
    seller_present_pct: float = 0.0
    non_seller_present_pct: float = 0.0
    non_seller_count_max: int = 0
    bill_motion_detected: bool = False
    bill_bg_change_detected: bool = False
    frame_count: int = 0


class TimelineEvent(BaseModel):
    ts: str
    source: str  # "pos" or "cv"
    type: str  # begin_transaction, sale_line, payment_line, commit, customer_entered, receipt_detected, etc.
    data: dict = Field(default_factory=dict)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd poc && python -m pytest tests/test_models.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add poc/backend/models.py poc/tests/test_models.py
git commit -m "feat(poc): add Pydantic models for transactions, alerts, CV windows"
```

---

### Task 3: JSONL storage

**Files:**
- Create: `poc/backend/storage.py`
- Create: `poc/tests/test_storage.py`

- [ ] **Step 1: Write tests**

```python
# poc/tests/test_storage.py
import json
import tempfile
import os
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
```

- [ ] **Step 2: Run tests — verify fail**

```bash
cd poc && python -m pytest tests/test_storage.py -v
```

- [ ] **Step 3: Implement storage.py**

```python
# poc/backend/storage.py
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


class Storage:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "events").mkdir(exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._seen: set[str] = set()

    def _lock_for(self, name: str) -> threading.Lock:
        if name not in self._locks:
            self._locks[name] = threading.Lock()
        return self._locks[name]

    def _filepath(self, name: str) -> Path:
        return self.data_dir / f"{name}.jsonl"

    def append(self, name: str, record: dict):
        with self._lock_for(name):
            with open(self._filepath(name), "a") as f:
                f.write(json.dumps(record, default=str) + "\n")

    def read(self, name: str) -> list[dict]:
        path = self._filepath(name)
        if not path.exists():
            return []
        with self._lock_for(name):
            records = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            return records

    def update(self, name: str, record_id: str, updates: dict):
        with self._lock_for(name):
            path = self._filepath(name)
            if not path.exists():
                return
            records = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            for r in records:
                if r.get("id") == record_id:
                    r.update(updates)
                    break
            with open(path, "w") as f:
                for r in records:
                    f.write(json.dumps(r, default=str) + "\n")

    def append_event(self, event: dict):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.data_dir / "events" / f"{today}.jsonl"
        with self._lock_for("events"):
            with open(path, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")

    def read_events(self, date: str = None) -> list[dict]:
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.data_dir / "events" / f"{date}.jsonl"
        if not path.exists():
            return []
        with open(path) as f:
            return [json.loads(line.strip()) for line in f if line.strip()]

    def _dedup_key(self, event: dict) -> str:
        return f"{event.get('transactionSessionId', '')}:{event.get('event', '')}:{event.get('lineNumber', '')}"

    def is_duplicate(self, event: dict) -> bool:
        return self._dedup_key(event) in self._seen

    def mark_seen(self, event: dict):
        self._seen.add(self._dedup_key(event))
```

- [ ] **Step 4: Run tests — verify pass**

```bash
cd poc && python -m pytest tests/test_storage.py -v
```

- [ ] **Step 5: Commit**

```bash
git add poc/backend/storage.py poc/tests/test_storage.py
git commit -m "feat(poc): add JSONL storage with WAL, dedup, and update"
```

---

### Task 4: Config loader

**Files:**
- Create: `poc/backend/config.py`

- [ ] **Step 1: Implement config.py**

```python
# poc/backend/config.py
import json
import os
import time
import threading
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class PosZoneConfig:
    zone_id: str
    seller_zone: list[list[int]]
    bill_zone: list[list[int]]


@dataclass
class CameraEntry:
    seller_window_id: str
    store_id: str
    pos_terminal: str
    camera_id: str
    rtsp_url: str
    xprotect_device_id: str
    multi_pos: bool
    pos_zones: list[PosZoneConfig]


@dataclass
class StoreEntry:
    cin: str
    name: str
    pos_system: str


class Config:
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.stores: list[StoreEntry] = []
        self.cameras: list[CameraEntry] = []
        self.rules: dict = {}
        self._last_modified: dict[str, float] = {}
        self.reload()

    def reload(self):
        self._load_stores()
        self._load_cameras()
        self._load_rules()

    def _load_stores(self):
        path = self.config_dir / "stores.json"
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            self.stores = [StoreEntry(cin=s["cin"], name=s["name"], pos_system=s["pos_system"]) for s in data]

    def _load_cameras(self):
        path = self.config_dir / "camera_mapping.json"
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            self.cameras = []
            for c in data:
                zones = [
                    PosZoneConfig(
                        zone_id=z["zone_id"],
                        seller_zone=z["seller_zone"],
                        bill_zone=z["bill_zone"],
                    )
                    for z in c.get("zones", {}).get("pos_zones", [])
                ]
                self.cameras.append(CameraEntry(
                    seller_window_id=c["seller_window_id"],
                    store_id=c["store_id"],
                    pos_terminal=c["pos_terminal"],
                    camera_id=c["camera_id"],
                    rtsp_url=c.get("rtsp_url", ""),
                    xprotect_device_id=c.get("xprotect_device_id", ""),
                    multi_pos=c.get("multi_pos", False),
                    pos_zones=zones,
                ))

    def _load_rules(self):
        path = self.config_dir / "rule_config.json"
        if path.exists():
            with open(path) as f:
                self.rules = json.load(f)

    def save_stores(self):
        path = self.config_dir / "stores.json"
        with open(path, "w") as f:
            json.dump([{"cin": s.cin, "name": s.name, "pos_system": s.pos_system} for s in self.stores], f, indent=2)

    def save_cameras(self):
        path = self.config_dir / "camera_mapping.json"
        data = []
        for c in self.cameras:
            data.append({
                "seller_window_id": c.seller_window_id,
                "store_id": c.store_id,
                "pos_terminal": c.pos_terminal,
                "camera_id": c.camera_id,
                "rtsp_url": c.rtsp_url,
                "xprotect_device_id": c.xprotect_device_id,
                "multi_pos": c.multi_pos,
                "zones": {
                    "pos_zones": [
                        {"zone_id": z.zone_id, "seller_zone": z.seller_zone, "bill_zone": z.bill_zone}
                        for z in c.pos_zones
                    ]
                }
            })
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def save_rules(self):
        path = self.config_dir / "rule_config.json"
        with open(path, "w") as f:
            json.dump(self.rules, f, indent=2)

    def get_camera_by_seller_window(self, seller_window_id: str) -> CameraEntry | None:
        for c in self.cameras:
            if c.seller_window_id == seller_window_id:
                return c
        return None

    def get_camera_by_id(self, camera_id: str) -> CameraEntry | None:
        for c in self.cameras:
            if c.camera_id == camera_id:
                return c
        return None

    def get_store_name(self, cin: str) -> str:
        for s in self.stores:
            if s.cin == cin:
                return s.name
        return cin

    def has_changed(self) -> bool:
        changed = False
        for name in ("stores.json", "camera_mapping.json", "rule_config.json"):
            path = self.config_dir / name
            if path.exists():
                mtime = path.stat().st_mtime
                if self._last_modified.get(name) != mtime:
                    self._last_modified[name] = mtime
                    changed = True
        return changed
```

- [ ] **Step 2: Commit**

```bash
git add poc/backend/config.py
git commit -m "feat(poc): add config loader with stores, cameras, rules, hot reload"
```

---

### Task 5: Transaction assembler

**Files:**
- Create: `poc/backend/assembler.py`
- Create: `poc/tests/test_assembler.py`

- [ ] **Step 1: Write tests**

```python
# poc/tests/test_assembler.py
from backend.assembler import TransactionAssembler


def test_begin_creates_session():
    asm = TransactionAssembler()
    asm.begin({
        "transactionSessionId": "s1",
        "storeIdentifier": "NDCIN1223",
        "posTerminalNo": "POS 3",
        "cashier": "EMP-042",
        "transactionType": "CompletedNormally",
        "employeePurchase": False,
        "transactionTimeStamp": "2026-04-16T10:02:00Z",
    })
    assert "s1" in asm.sessions
    assert asm.sessions["s1"].store_id == "NDCIN1223"
    assert asm.sessions["s1"].status == "open"


def test_add_sale_line():
    asm = TransactionAssembler()
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    asm.add_sale_line({
        "transactionSessionId": "s1",
        "itemDescription": "Burger",
        "itemQuantity": 1,
        "itemUnitPrice": 249.0,
        "totalAmount": 249.0,
        "scanAttribute": "Auto",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
    })
    assert len(asm.sessions["s1"].items) == 1
    assert asm.sessions["s1"].items[0].item_description == "Burger"


def test_add_payment_line():
    asm = TransactionAssembler()
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    asm.add_payment_line({
        "transactionSessionId": "s1",
        "lineAttribute": "Cash",
        "paymentDescription": "Cash",
        "amount": 500.0,
    })
    assert len(asm.sessions["s1"].payments) == 1
    assert asm.sessions["s1"].payments[0].line_attribute == "Cash"


def test_commit_returns_session():
    asm = TransactionAssembler()
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    asm.add_sale_line({
        "transactionSessionId": "s1",
        "itemDescription": "Burger",
        "itemQuantity": 1,
        "itemUnitPrice": 249.0,
        "totalAmount": 249.0,
        "scanAttribute": "Auto",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
    })
    txn = asm.commit({"transactionSessionId": "s1", "transactionNumber": "BILL-001"})
    assert txn is not None
    assert txn.status == "committed"
    assert txn.bill_number == "BILL-001"
    assert "s1" not in asm.sessions  # removed after commit


def test_commit_unknown_session_returns_none():
    asm = TransactionAssembler()
    txn = asm.commit({"transactionSessionId": "unknown"})
    assert txn is None


def test_buffered_event_before_begin():
    asm = TransactionAssembler()
    # sale line arrives before begin — gets buffered
    asm.add_sale_line({
        "transactionSessionId": "s1",
        "itemDescription": "Fries",
        "itemQuantity": 1,
        "itemUnitPrice": 99.0,
        "totalAmount": 99.0,
        "scanAttribute": "ManuallyEntered",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
    })
    assert "s1" not in asm.sessions
    assert len(asm._buffer) == 1

    # now begin arrives — buffered events get attached
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    assert len(asm.sessions["s1"].items) == 1
    assert asm.sessions["s1"].items[0].scan_attribute == "ManuallyEntered"


def test_check_expired():
    asm = TransactionAssembler(timeout_seconds=0)  # immediate expiry for test
    asm.begin({"transactionSessionId": "s1", "storeIdentifier": "X", "posTerminalNo": "P1"})
    asm.add_sale_line({
        "transactionSessionId": "s1",
        "itemDescription": "Burger",
        "itemQuantity": 1,
        "itemUnitPrice": 249.0,
        "totalAmount": 249.0,
        "scanAttribute": "Auto",
        "itemAttribute": "None",
        "discountType": "NoLineDiscount",
        "discount": 0,
    })
    expired = asm.check_expired()
    assert len(expired) == 1
    assert expired[0].status == "expired"
    assert "s1" not in asm.sessions
```

- [ ] **Step 2: Run tests — verify fail**

```bash
cd poc && python -m pytest tests/test_assembler.py -v
```

- [ ] **Step 3: Implement assembler.py**

```python
# poc/backend/assembler.py
from backend.models import (
    TransactionSession, SaleLine, PaymentLine, TotalLine, TransactionEvent, utc_now
)
from collections import defaultdict
from datetime import datetime, timezone
import time


class TransactionAssembler:
    def __init__(self, timeout_seconds: int = 1800):
        self.sessions: dict[str, TransactionSession] = {}
        self.timeout = timeout_seconds
        self._buffer: list[dict] = []  # events that arrived before their BeginTransaction
        self._session_times: dict[str, float] = {}  # session_id -> creation time (monotonic)

    def begin(self, payload: dict):
        session_id = payload["transactionSessionId"]
        txn = TransactionSession(
            id=session_id,
            store_id=payload.get("storeIdentifier", ""),
            pos_terminal=payload.get("posTerminalNo", ""),
            cashier_id=payload.get("cashier", ""),
            transaction_type=payload.get("transactionType", "CompletedNormally"),
            employee_purchase=payload.get("employeePurchase", False),
            outside_opening_hours=payload.get("outsideOpeningHours", "InsideOpeningHours"),
            started_at=payload.get("transactionTimeStamp"),
            source="push_assembled",
            status="open",
        )
        self.sessions[session_id] = txn
        self._session_times[session_id] = time.monotonic()
        self._flush_buffer(session_id)

    def add_sale_line(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.items.append(SaleLine.from_nukkad(payload))
        else:
            self._buffer.append(("sale_line", payload))

    def add_payment_line(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.payments.append(PaymentLine.from_nukkad(payload))
        else:
            self._buffer.append(("payment_line", payload))

    def add_total_line(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.totals.append(TotalLine.from_nukkad(payload))
        else:
            self._buffer.append(("total_line", payload))

    def add_event(self, payload: dict):
        session = self._get_session(payload)
        if session:
            session.events.append(TransactionEvent.from_nukkad(payload))
        else:
            self._buffer.append(("event", payload))

    def commit(self, payload: dict) -> TransactionSession | None:
        session_id = payload.get("transactionSessionId", "")
        session = self.sessions.pop(session_id, None)
        self._session_times.pop(session_id, None)
        if session:
            session.bill_number = payload.get("transactionNumber", "")
            session.status = "committed"
            session.committed_at = utc_now()
            return session
        return None

    def check_expired(self) -> list[TransactionSession]:
        now = time.monotonic()
        expired = []
        expired_ids = []
        for sid, created in self._session_times.items():
            if now - created > self.timeout:
                expired_ids.append(sid)
        for sid in expired_ids:
            session = self.sessions.pop(sid, None)
            self._session_times.pop(sid, None)
            if session:
                session.status = "expired"
                expired.append(session)
        return expired

    def _get_session(self, payload: dict) -> TransactionSession | None:
        session_id = payload.get("transactionSessionId", "")
        return self.sessions.get(session_id)

    def _flush_buffer(self, session_id: str):
        remaining = []
        for event_type, payload in self._buffer:
            if payload.get("transactionSessionId") == session_id:
                session = self.sessions[session_id]
                if event_type == "sale_line":
                    session.items.append(SaleLine.from_nukkad(payload))
                elif event_type == "payment_line":
                    session.payments.append(PaymentLine.from_nukkad(payload))
                elif event_type == "total_line":
                    session.totals.append(TotalLine.from_nukkad(payload))
                elif event_type == "event":
                    session.events.append(TransactionEvent.from_nukkad(payload))
            else:
                remaining.append((event_type, payload))
        self._buffer = remaining
```

- [ ] **Step 4: Run tests — verify pass**

```bash
cd poc && python -m pytest tests/test_assembler.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add poc/backend/assembler.py poc/tests/test_assembler.py
git commit -m "feat(poc): add transaction assembler with buffering and expiry"
```

---

### Task 6: Fraud engine (29 rules)

**Files:**
- Create: `poc/backend/fraud.py`
- Create: `poc/tests/test_fraud.py`

- [ ] **Step 1: Write tests**

```python
# poc/tests/test_fraud.py
from backend.fraud import FraudEngine
from backend.models import TransactionSession, SaleLine, PaymentLine, Alert
import json


def _make_config():
    with open("config/rule_config.json") as f:
        return json.load(f)


def _make_txn(**kwargs) -> TransactionSession:
    defaults = {"id": "TXN-001", "store_id": "NDCIN1223", "pos_terminal": "POS 3", "source": "push_assembled", "status": "committed"}
    defaults.update(kwargs)
    return TransactionSession(**defaults)


def test_negative_amount():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.totals = [type("T", (), {"line_attribute": "TotalAmountToBePaid", "amount": -50.0})()]
    alerts = engine.evaluate(txn)
    assert "5_negative_amount" in txn.triggered_rules
    assert txn.risk_level == "High"


def test_manual_entry():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.items = [SaleLine(scan_attribute="ManuallyEntered", item_description="Fries", total_amount=99)]
    alerts = engine.evaluate(txn)
    assert "8_manual_entry" in txn.triggered_rules


def test_manual_discount():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.items = [SaleLine(discount_type="ManuallyEnteredValue", discount=30, total_amount=70)]
    alerts = engine.evaluate(txn)
    assert "10_manual_discount" in txn.triggered_rules


def test_drawer_opened():
    engine = FraudEngine(_make_config())
    txn = _make_txn(transaction_type="DrawerOpenedOutsideATransaction")
    alerts = engine.evaluate(txn)
    assert "12_drawer_opened" in txn.triggered_rules
    assert txn.risk_level == "High"


def test_null_transaction():
    engine = FraudEngine(_make_config())
    txn = _make_txn()  # no items
    alerts = engine.evaluate(txn)
    assert "14_null_transaction" in txn.triggered_rules


def test_void_no_customer():
    engine = FraudEngine(_make_config())
    txn = _make_txn(cv_non_seller_present=False, cv_confidence="HIGH")
    txn.items = [SaleLine(item_attribute="CancellationWithinTransaction", total_amount=100)]
    alerts = engine.evaluate(txn)
    assert "26_void_no_customer" in txn.triggered_rules
    assert txn.risk_level == "High"


def test_genuine_transaction():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.items = [SaleLine(scan_attribute="Auto", item_description="Burger", total_amount=249)]
    txn.payments = [PaymentLine(line_attribute="Cash", amount=249)]
    alerts = engine.evaluate(txn)
    assert txn.risk_level == "Low"
    assert len(alerts) == 0


def test_risk_escalation_two_mediums():
    engine = FraudEngine(_make_config())
    txn = _make_txn()
    txn.items = [
        SaleLine(scan_attribute="ManuallyEntered", item_description="Fries", total_amount=99),
        SaleLine(discount_type="ManuallyEnteredValue", discount=30, total_amount=70),
    ]
    alerts = engine.evaluate(txn)
    # manual entry (medium) + manual discount (medium) = still medium (need 3 for high)
    assert txn.risk_level == "Medium"


def test_disabled_rule_skipped():
    config = _make_config()
    config["rules"]["8_manual_entry"]["enabled"] = False
    engine = FraudEngine(config)
    txn = _make_txn()
    txn.items = [SaleLine(scan_attribute="ManuallyEntered", item_description="Fries", total_amount=99)]
    alerts = engine.evaluate(txn)
    assert "8_manual_entry" not in txn.triggered_rules
```

- [ ] **Step 2: Run tests — verify fail**

```bash
cd poc && python -m pytest tests/test_fraud.py -v
```

- [ ] **Step 3: Implement fraud.py**

```python
# poc/backend/fraud.py
from backend.models import TransactionSession, Alert, gen_id, utc_now
from datetime import datetime, timezone
from typing import Optional


# Rule definitions: (id, name, evaluate_fn, default_risk)
RULE_DEFAULTS = {
    "1_high_discount": "Medium",
    "2_refund_excess": "Medium",
    "3_complementary": "Low",
    "4_void_cancelled": "Medium",
    "5_negative_amount": "High",
    "6_high_value": "Low",
    "7_bulk_purchase": "Low",
    "8_manual_entry": "Medium",
    "9_manual_price": "Medium",
    "10_manual_discount": "Medium",
    "11_self_granted_discount": "Medium",
    "12_drawer_opened": "High",
    "13_bill_reprint": "Medium",
    "14_null_transaction": "Medium",
    "15_post_bill_cancel": "High",
    "16_return_not_recent": "Medium",
    "17_exchange_no_match": "Medium",
    "18_employee_purchase": "Low",
    "19_void_percentage": "Medium",
    "20_outside_hours": "Medium",
    "21_credit_note": "Medium",
    "22_manual_card": "Medium",
    "23_full_return": "High",
    "24_missing_pos": "High",
    "25_pos_idle": "Low",
    "26_void_no_customer": "High",
    "27_return_no_customer": "High",
    "28_drawer_no_customer": "High",
    "29_bill_not_generated": "Medium",
}


class FraudEngine:
    def __init__(self, config: dict):
        self.config = config
        self.rules_config = config.get("rules", {})
        self._last_nukkad_event: dict[str, datetime] = {}  # store_id -> last event time

    def record_nukkad_event(self, store_id: str):
        self._last_nukkad_event[store_id] = utc_now()

    def is_feed_down(self, store_id: str) -> bool:
        last = self._last_nukkad_event.get(store_id)
        if last is None:
            return True
        delta = (utc_now() - last).total_seconds()
        return delta > self.config.get("feed_down_minutes", 10) * 60

    def evaluate(self, txn: TransactionSession) -> list[Alert]:
        triggered: list[tuple[str, str]] = []  # (rule_id, risk)

        # EPOS-only rules
        self._check_epos_rules(txn, triggered)

        # Cross-validation rules (only if CV data available)
        if txn.cv_confidence and txn.cv_confidence not in ("UNAVAILABLE", "UNMAPPED", ""):
            self._check_cross_validation_rules(txn, triggered)

        # Apply results
        txn.triggered_rules = [r[0] for r in triggered]
        txn.risk_level = self._calculate_risk(triggered)

        if txn.risk_level in ("High", "Medium"):
            alert = Alert(
                transaction_id=txn.id,
                store_id=txn.store_id,
                pos_zone=txn.pos_terminal,
                cashier_id=txn.cashier_id,
                risk_level=txn.risk_level,
                triggered_rules=txn.triggered_rules,
                camera_id=txn.camera_id,
                device_id=txn.device_id,
            )
            return [alert]
        return []

    def _is_enabled(self, rule_id: str) -> bool:
        return self.rules_config.get(rule_id, {}).get("enabled", True)

    def _check_epos_rules(self, txn: TransactionSession, triggered: list):
        # 1. High discount
        if self._is_enabled("1_high_discount"):
            threshold = self.config.get("discount_threshold_percent", 20)
            for item in txn.items:
                if item.total_amount > 0 and item.discount > 0:
                    pct = (item.discount / (item.total_amount + item.discount)) * 100
                    if pct > threshold:
                        triggered.append(("1_high_discount", "Medium"))
                        break

        # 2. Refund / excess cash
        if self._is_enabled("2_refund_excess"):
            threshold = self.config.get("refund_amount_threshold", 0)
            for pay in txn.payments:
                if pay.line_attribute == "ReturnCash" and pay.amount > threshold:
                    triggered.append(("2_refund_excess", "Medium"))
                    break

        # 3. Complementary
        if self._is_enabled("3_complementary") and txn.transaction_type == "Complementary":
            triggered.append(("3_complementary", "Low"))

        # 4. Void / cancelled
        if self._is_enabled("4_void_cancelled"):
            if txn.transaction_type in ("Cancelled", "Suspended"):
                triggered.append(("4_void_cancelled", "Medium"))
            for ev in txn.events:
                if ev.line_attribute == "TransactionCancelled":
                    triggered.append(("4_void_cancelled", "Medium"))
                    break

        # 5. Negative amount
        if self._is_enabled("5_negative_amount"):
            for t in txn.totals:
                if t.line_attribute == "TotalAmountToBePaid" and t.amount < 0:
                    triggered.append(("5_negative_amount", "High"))
                    break

        # 6. High value
        if self._is_enabled("6_high_value"):
            threshold = self.config.get("high_value_threshold", 2000)
            for t in txn.totals:
                if t.line_attribute == "TotalAmountToBePaid" and t.amount > threshold:
                    triggered.append(("6_high_value", "Low"))
                    break

        # 7. Bulk purchase
        if self._is_enabled("7_bulk_purchase"):
            threshold = self.config.get("bulk_quantity_threshold", 10)
            total_qty = sum(item.item_quantity for item in txn.items)
            if total_qty > threshold:
                triggered.append(("7_bulk_purchase", "Low"))

        # 8. Manual entry
        if self._is_enabled("8_manual_entry"):
            if any(item.scan_attribute == "ManuallyEntered" for item in txn.items):
                triggered.append(("8_manual_entry", "Medium"))

        # 9. Manual price
        if self._is_enabled("9_manual_price"):
            if any(item.scan_attribute == "ModifiedUnitPrice" for item in txn.items):
                triggered.append(("9_manual_price", "Medium"))

        # 10. Manual discount
        if self._is_enabled("10_manual_discount"):
            if any(item.discount_type in ("ManuallyEnteredValue", "ManuallyEnteredPercentage") for item in txn.items):
                triggered.append(("10_manual_discount", "Medium"))

        # 11. Self-granted discount
        if self._is_enabled("11_self_granted_discount"):
            for item in txn.items:
                if item.granted_by and item.granted_by == txn.cashier_id and item.discount > 0:
                    triggered.append(("11_self_granted_discount", "Medium"))
                    break

        # 12. Drawer opened outside transaction
        if self._is_enabled("12_drawer_opened"):
            if txn.transaction_type == "DrawerOpenedOutsideATransaction":
                triggered.append(("12_drawer_opened", "High"))

        # 14. Null transaction
        if self._is_enabled("14_null_transaction"):
            if txn.status == "committed" and len(txn.items) == 0:
                triggered.append(("14_null_transaction", "Medium"))

        # 15. Post-bill cancellation
        if self._is_enabled("15_post_bill_cancel"):
            if txn.transaction_type == "CancellationOfPrevious":
                triggered.append(("15_post_bill_cancel", "High"))

        # 16. Return not recently sold
        if self._is_enabled("16_return_not_recent"):
            if any(item.item_attribute == "ReturnNotRecentlySold" for item in txn.items):
                triggered.append(("16_return_not_recent", "Medium"))

        # 17. Exchange without matching line
        if self._is_enabled("17_exchange_no_match"):
            if any(item.item_attribute == "ExchangeSlipWithoutMatchingLine" for item in txn.items):
                triggered.append(("17_exchange_no_match", "Medium"))

        # 18. Employee purchase
        if self._is_enabled("18_employee_purchase"):
            if txn.employee_purchase:
                triggered.append(("18_employee_purchase", "Low"))

        # 19. Per-item void percentage
        if self._is_enabled("19_void_percentage"):
            threshold = self.config.get("void_percentage_threshold", 50)
            if txn.items:
                void_count = sum(1 for item in txn.items if item.item_attribute == "CancellationWithinTransaction")
                pct = (void_count / len(txn.items)) * 100
                if pct > threshold:
                    triggered.append(("19_void_percentage", "Medium"))

        # 20. Outside opening hours
        if self._is_enabled("20_outside_hours"):
            if txn.outside_opening_hours != "InsideOpeningHours":
                triggered.append(("20_outside_hours", "Medium"))

        # 21. Credit note payment
        if self._is_enabled("21_credit_note"):
            if any(pay.line_attribute == "CreditNotePayment" for pay in txn.payments):
                triggered.append(("21_credit_note", "Medium"))

        # 22. Manual credit card entry (speculative)
        if self._is_enabled("22_manual_card"):
            # fires if card payment exists without approval code — needs Nukkad clarification
            pass

        # 23. Full return
        if self._is_enabled("23_full_return"):
            if txn.items and all(item.item_attribute == "ReturnItem" for item in txn.items):
                triggered.append(("23_full_return", "High"))

    def _check_cross_validation_rules(self, txn: TransactionSession, triggered: list):
        # 26. Void without customer
        if self._is_enabled("26_void_no_customer"):
            has_void = any(item.item_attribute == "CancellationWithinTransaction" for item in txn.items)
            if has_void and txn.cv_non_seller_present is False:
                triggered.append(("26_void_no_customer", "High"))

        # 27. Return without customer
        if self._is_enabled("27_return_no_customer"):
            has_return = any(item.item_attribute == "ReturnItem" for item in txn.items)
            if has_return and txn.cv_non_seller_present is False:
                triggered.append(("27_return_no_customer", "High"))

        # 28. Drawer open without customer
        if self._is_enabled("28_drawer_no_customer"):
            if txn.transaction_type == "DrawerOpenedOutsideATransaction" and txn.cv_non_seller_present is False:
                triggered.append(("28_drawer_no_customer", "High"))

        # 29. Bill not generated (CV says no receipt)
        if self._is_enabled("29_bill_not_generated"):
            if txn.status == "committed" and txn.cv_receipt_detected is False:
                triggered.append(("29_bill_not_generated", "Medium"))

    def _calculate_risk(self, triggered: list[tuple[str, str]]) -> str:
        if not triggered:
            return "Low"
        risks = [r[1] for r in triggered]
        if "High" in risks:
            return "High"
        medium_count = risks.count("Medium")
        if medium_count >= 3:
            return "High"
        if medium_count >= 1:
            return "Medium"
        return "Low"
```

- [ ] **Step 4: Run tests — verify pass**

```bash
cd poc && python -m pytest tests/test_fraud.py -v
```

Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add poc/backend/fraud.py poc/tests/test_fraud.py
git commit -m "feat(poc): add fraud engine with 29 rules + risk escalation matrix"
```

---

### Task 7: WebSocket manager + FastAPI app shell

**Files:**
- Create: `poc/backend/ws.py`
- Create: `poc/backend/main.py`

- [ ] **Step 1: Create ws.py**

```python
# poc/backend/ws.py
from fastapi import WebSocket
import json


class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self.connections.remove(ws)

    async def broadcast(self, message_type: str, data: dict):
        msg = json.dumps({"type": message_type, "data": data}, default=str)
        disconnected = []
        for ws in self.connections:
            try:
                await ws.send_text(msg)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.connections.remove(ws)
```

- [ ] **Step 2: Create main.py — FastAPI app shell**

```python
# poc/backend/main.py
import asyncio
import os
import sys
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

# Resolve paths relative to poc/
POC_DIR = Path(__file__).parent.parent
CONFIG_DIR = POC_DIR / "config"
DATA_DIR = POC_DIR / "data"

# Globals
config = Config(config_dir=str(CONFIG_DIR))
storage = Storage(data_dir=str(DATA_DIR))
assembler = TransactionAssembler()
fraud_engine = FraudEngine(config.rules)
ws_manager = ConnectionManager()


async def config_watcher():
    """Poll config files for changes every 10 seconds."""
    while True:
        await asyncio.sleep(10)
        if config.has_changed():
            config.reload()
            fraud_engine.__init__(config.rules)


async def expiry_checker():
    """Check for expired transaction sessions every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        expired = assembler.check_expired()
        for txn in expired:
            txn.risk_level = "Medium"
            txn.triggered_rules = ["abandoned_transaction"]
            alert_data = {
                "id": txn.id,
                "store_id": txn.store_id,
                "risk_level": "Medium",
                "triggered_rules": ["abandoned_transaction"],
            }
            storage.append("transactions", txn.model_dump())
            storage.append("alerts", alert_data)
            await ws_manager.broadcast("NEW_ALERT", alert_data)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "buffer").mkdir(exist_ok=True)
    (DATA_DIR / "snippets").mkdir(exist_ok=True)
    (DATA_DIR / "events").mkdir(exist_ok=True)

    tasks = [
        asyncio.create_task(config_watcher()),
        asyncio.create_task(expiry_checker()),
    ]
    yield
    # Shutdown
    for t in tasks:
        t.cancel()


app = FastAPI(title="RLCC POC", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and include routers
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
    return storage.read("transactions")


@app.get("/api/transactions/{txn_id}")
async def get_transaction(txn_id: str):
    for txn in storage.read("transactions"):
        if txn.get("id") == txn_id:
            return txn
    return {"error": "not found"}


@app.get("/api/alerts")
async def list_alerts():
    return storage.read("alerts")


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


# Serve dashboard static build if it exists
dashboard_build = POC_DIR / "dashboard" / "dist"
if dashboard_build.exists():
    app.mount("/", StaticFiles(directory=str(dashboard_build), html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8001, reload=True)
```

- [ ] **Step 3: Create stub receiver.py (filled in Task 8)**

```python
# poc/backend/receiver.py
from fastapi import APIRouter

router = APIRouter()
```

- [ ] **Step 4: Create stub camera_api.py (filled in later)**

```python
# poc/backend/camera_api.py
from fastapi import APIRouter

router = APIRouter()
```

- [ ] **Step 5: Test the app starts**

```bash
cd poc && python -m backend.main
```

Expected: Uvicorn starts on `http://0.0.0.0:8001`. Hit `http://localhost:8001/api/stores` in browser — should return the 5 stores JSON. Ctrl+C to stop.

- [ ] **Step 6: Commit**

```bash
git add poc/backend/ws.py poc/backend/main.py poc/backend/receiver.py poc/backend/camera_api.py
git commit -m "feat(poc): add FastAPI app shell with WebSocket, config watcher, expiry checker"
```

---

### Task 8: Nukkad push event receiver

**Files:**
- Modify: `poc/backend/receiver.py`
- Create: `poc/tests/test_receiver.py`

- [ ] **Step 1: Write tests**

```python
# poc/tests/test_receiver.py
import json
import pytest
from httpx import AsyncClient, ASGITransport
from backend.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_begin_transaction():
    payload = {
        "event": "BeginTransactionWithTillLookup",
        "storeIdentifier": "NDCIN1223",
        "posTerminalNo": "POS 3",
        "transactionSessionId": "test-session-001",
        "cashier": "EMP-042",
        "transactionType": "CompletedNormally",
        "employeePurchase": False,
    }
    # Nukkad sends stringified JSON
    stringified = json.dumps(json.dumps(payload))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/rlcc/launch-event",
            content=stringified,
            headers={"Content-Type": "application/json", "x-authorization-key": "test"},
        )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Success"


@pytest.mark.anyio
async def test_invalid_json():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/rlcc/launch-event",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 400
```

- [ ] **Step 2: Implement receiver.py**

```python
# poc/backend/receiver.py
import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/v1/rlcc/launch-event")
async def receive_event(request: Request):
    from backend.main import assembler, storage, fraud_engine, ws_manager, config
    from backend.models import Alert

    try:
        body = await request.body()
        body_str = body.decode("utf-8")
        # Nukkad sends stringified JSON — parse outer string, then JSON decode
        try:
            inner = json.loads(body_str)
            if isinstance(inner, str):
                payload = json.loads(inner)
            else:
                payload = inner
        except (json.JSONDecodeError, TypeError):
            return JSONResponse(status_code=400, content={"message": "Invalid JSON"})
    except Exception:
        return JSONResponse(status_code=400, content={"message": "Invalid request body"})

    event_type = payload.get("event", "")

    # Persist raw event to WAL
    storage.append_event(payload)

    # Deduplicate
    if storage.is_duplicate(payload):
        return {"status": 200, "message": "duplicate, ignored"}
    storage.mark_seen(payload)

    # Record Nukkad activity for feed-down detection
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
            # Correlate with CV (if cv_consumer is running)
            # For now, run fraud engine directly
            alerts = fraud_engine.evaluate(txn)

            # Persist
            storage.append("transactions", txn.model_dump())
            await ws_manager.broadcast("NEW_TRANSACTION", {"id": txn.id, "store_id": txn.store_id, "risk_level": txn.risk_level})

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
            camera_id="",
        )
        storage.append("alerts", alert.model_dump())
        await ws_manager.broadcast("NEW_ALERT", alert.model_dump())

    return {"status": 200, "message": "Success"}
```

- [ ] **Step 3: Run tests — verify pass**

```bash
cd poc && python -m pytest tests/test_receiver.py -v
```

- [ ] **Step 4: Commit**

```bash
git add poc/backend/receiver.py poc/tests/test_receiver.py
git commit -m "feat(poc): add Nukkad push event receiver with stringified JSON parsing"
```

---

### Task 9: Nukkad event emulator

**Files:**
- Create: `poc/emulator/scenarios.py`
- Create: `poc/emulator/nukkad_emulator.py`

- [ ] **Step 1: Create scenarios.py**

```python
# poc/emulator/scenarios.py
import random

PRODUCTS = [
    {"name": "Chicken Burger", "price": 249, "code": "CB001"},
    {"name": "Veg Wrap", "price": 179, "code": "VW002"},
    {"name": "Coke 500ml", "price": 60, "code": "CK003"},
    {"name": "French Fries", "price": 99, "code": "FF004"},
    {"name": "Paneer Tikka", "price": 329, "code": "PT005"},
    {"name": "Masala Dosa", "price": 149, "code": "MD006"},
    {"name": "Filter Coffee", "price": 89, "code": "FC007"},
    {"name": "Biryani", "price": 299, "code": "BR008"},
    {"name": "Samosa", "price": 49, "code": "SM009"},
    {"name": "Ice Cream", "price": 129, "code": "IC010"},
]

CASHIERS = ["EMP-042", "EMP-071", "EMP-103", "EMP-088", "EMP-056"]
PAY_MODES = [("Cash", 0.4), ("CreditCard", 0.3), ("UPI", 0.2), ("Phonepe", 0.1)]


def pick_items(count: int = None) -> list[dict]:
    if count is None:
        count = random.randint(1, 5)
    items = random.sample(PRODUCTS, min(count, len(PRODUCTS)))
    return [{"name": p["name"], "price": p["price"], "code": p["code"], "qty": random.randint(1, 3)} for p in items]


def pick_pay_mode() -> str:
    modes, weights = zip(*PAY_MODES)
    return random.choices(modes, weights=weights, k=1)[0]


def pick_cashier() -> str:
    return random.choice(CASHIERS)


# Fraud scenario generators
def scenario_manual_entry(items: list[dict]) -> list[dict]:
    """Mark one random item as manually entered."""
    idx = random.randint(0, len(items) - 1)
    items[idx]["scan_attribute"] = "ManuallyEntered"
    return items


def scenario_manual_discount(items: list[dict], cashier: str) -> list[dict]:
    """Add a manual discount to one item, self-granted."""
    idx = random.randint(0, len(items) - 1)
    items[idx]["discount_type"] = "ManuallyEnteredValue"
    items[idx]["discount"] = round(items[idx]["price"] * 0.3, 2)  # 30% off
    items[idx]["granted_by"] = cashier  # self-granted
    return items


def scenario_high_discount(items: list[dict]) -> list[dict]:
    """Apply >20% discount."""
    idx = random.randint(0, len(items) - 1)
    items[idx]["discount_type"] = "AutoGeneratedPercentage"
    items[idx]["discount"] = round(items[idx]["price"] * 0.35, 2)  # 35% off
    return items


def scenario_void_item(items: list[dict]) -> list[dict]:
    """Mark one item as voided mid-transaction."""
    idx = random.randint(0, len(items) - 1)
    items[idx]["item_attribute"] = "CancellationWithinTransaction"
    return items


def scenario_return_not_recent(items: list[dict]) -> list[dict]:
    """Mark one item as return not recently sold."""
    idx = random.randint(0, len(items) - 1)
    items[idx]["item_attribute"] = "ReturnNotRecentlySold"
    return items


FRAUD_SCENARIOS = {
    "manual_entry": scenario_manual_entry,
    "high_discount": scenario_high_discount,
    "void_item": scenario_void_item,
    "return_not_recent": scenario_return_not_recent,
}
```

- [ ] **Step 2: Create nukkad_emulator.py**

```python
# poc/emulator/nukkad_emulator.py
"""
Nukkad POS event emulator.
Generates realistic push event sequences and sends to the backend receiver.

Usage:
    python -m emulator.nukkad_emulator [--url http://localhost:8001] [--interval 10] [--fraud-rate 0.1]
"""
import argparse
import json
import time
import random
import uuid
import httpx
from datetime import datetime, timezone

from emulator.scenarios import (
    pick_items, pick_pay_mode, pick_cashier,
    scenario_manual_entry, scenario_manual_discount,
    scenario_high_discount, scenario_void_item,
    scenario_return_not_recent, FRAUD_SCENARIOS,
)

STORES = [
    {"cin": "NDCIN1223", "terminal": "POS 3"},
    {"cin": "NSCIN8227", "terminal": "POS4"},
    {"cin": "NDCIN1227", "terminal": "POS 1"},
    {"cin": "NDCIN1226", "terminal": "POS 2"},
    {"cin": "NDCIN1228", "terminal": "POS 1"},
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def send_event(client: httpx.Client, url: str, payload: dict):
    """Send stringified JSON (as Nukkad does)."""
    stringified = json.dumps(json.dumps(payload))
    resp = client.post(
        f"{url}/v1/rlcc/launch-event",
        content=stringified,
        headers={"Content-Type": "application/json", "x-authorization-key": "emulator"},
    )
    status = "OK" if resp.status_code == 200 else f"ERR {resp.status_code}"
    return status


def generate_transaction(client: httpx.Client, url: str, store: dict, fraud_scenario: str = None):
    session_id = str(uuid.uuid4().int)[:20]
    cashier = pick_cashier()
    items = pick_items()
    pay_mode = pick_pay_mode()

    # Apply fraud scenario if specified
    if fraud_scenario == "manual_entry":
        items = scenario_manual_entry(items)
    elif fraud_scenario == "manual_discount":
        items = scenario_manual_discount(items, cashier)
    elif fraud_scenario == "high_discount":
        items = scenario_high_discount(items)
    elif fraud_scenario == "void_item":
        items = scenario_void_item(items)
    elif fraud_scenario == "return_not_recent":
        items = scenario_return_not_recent(items)
    elif fraud_scenario == "null_transaction":
        items = []  # no items
    elif fraud_scenario == "drawer_opened":
        # Special: drawer event, not a normal transaction
        send_event(client, url, {
            "event": "BeginTransactionWithTillLookup",
            "storeIdentifier": store["cin"],
            "posTerminalNo": store["terminal"],
            "transactionSessionId": session_id,
            "transactionType": "DrawerOpenedOutsideATransaction",
            "cashier": cashier,
            "employeePurchase": False,
            "isForTillLookup": True,
            "isPreviousTransaction": False,
            "branch": store["cin"],
            "tillDescription": store["terminal"],
            "transactionNumber": "",
            "currencyCode": "INR",
        })
        send_event(client, url, {
            "event": "CommitTransaction",
            "storeIdentifier": store["cin"],
            "posTerminalNo": store["terminal"],
            "transactionSessionId": session_id,
        })
        print(f"  [{store['cin']}] DRAWER OPENED (no customer)")
        return
    elif fraud_scenario == "reprint":
        send_event(client, url, {
            "event": "BillReprint",
            "storeIdentifier": store["cin"],
            "posTerminalNo": store["terminal"],
            "branch": store["cin"],
            "tillDescription": store["terminal"],
            "transactionTimestamp": int(time.time() * 1000),
            "billNumber": f"BILL-{random.randint(1000, 9999)}",
            "cashier": cashier,
        })
        print(f"  [{store['cin']}] BILL REPRINT")
        return

    # 1. BeginTransaction
    send_event(client, url, {
        "event": "BeginTransactionWithTillLookup",
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "transactionSessionId": session_id,
        "cashier": cashier,
        "transactionType": "CompletedNormally",
        "employeePurchase": fraud_scenario == "employee_purchase",
        "isForTillLookup": True,
        "isPreviousTransaction": False,
        "branch": store["cin"],
        "tillDescription": store["terminal"],
        "transactionNumber": "",
        "currencyCode": "INR",
        "transactionTimeStamp": utc_now_iso(),
    })

    # 2. AddSaleLines
    total = 0
    for i, item in enumerate(items):
        line_total = item["price"] * item["qty"] - item.get("discount", 0)
        total += line_total
        send_event(client, url, {
            "event": "AddTransactionSaleLine",
            "storeIdentifier": store["cin"],
            "posTerminalNo": store["terminal"],
            "transactionSessionId": session_id,
            "lineTimeStamp": utc_now_iso(),
            "lineNumber": i + 1,
            "itemID": item["code"],
            "itemDescription": item["name"],
            "itemQuantity": item["qty"],
            "itemUnitPrice": item["price"],
            "totalAmount": line_total,
            "scanAttribute": item.get("scan_attribute", "Auto"),
            "itemAttribute": item.get("item_attribute", "None"),
            "discountType": item.get("discount_type", "NoLineDiscount"),
            "discount": item.get("discount", 0),
            "grantedBy": item.get("granted_by", ""),
            "printable": True,
            "isForTillLookup": True,
            "isPreviousTransaction": False,
        })
        time.sleep(0.1)  # simulate scanning delay

    # 3. AddPaymentLine
    send_event(client, url, {
        "event": "AddTransactionPaymentLine",
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "transactionSessionId": session_id,
        "lineTimeStamp": utc_now_iso(),
        "lineNumber": 1,
        "lineAttribute": pay_mode,
        "paymentDescription": pay_mode,
        "amount": round(total, 2),
        "currencyCode": "INR",
        "printable": True,
    })

    # 4. AddTotalLine
    send_event(client, url, {
        "event": "AddTransactionTotalLine",
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "transactionSessionId": session_id,
        "lineTimeStamp": utc_now_iso(),
        "lineNumber": 1,
        "lineAttribute": "TotalAmountToBePaid",
        "totalDescription": "Total Amount Payable",
        "amount": round(total, 2),
        "printable": True,
    })

    # 5. CommitTransaction
    bill_no = f"BILL-{random.randint(10000, 99999)}"
    send_event(client, url, {
        "event": "CommitTransaction",
        "storeIdentifier": store["cin"],
        "posTerminalNo": store["terminal"],
        "transactionSessionId": session_id,
        "transactionNumber": bill_no,
    })

    scenario_label = f" [{fraud_scenario}]" if fraud_scenario else ""
    print(f"  [{store['cin']}] {bill_no}: {len(items)} items, {pay_mode} ₹{total:.0f}{scenario_label}")


def main():
    parser = argparse.ArgumentParser(description="Nukkad POS Event Emulator")
    parser.add_argument("--url", default="http://localhost:8001", help="Backend URL")
    parser.add_argument("--interval", type=float, default=5, help="Seconds between transactions")
    parser.add_argument("--fraud-rate", type=float, default=0.15, help="Fraction of fraudulent transactions")
    args = parser.parse_args()

    fraud_types = [
        "manual_entry", "manual_discount", "high_discount", "void_item",
        "return_not_recent", "null_transaction", "drawer_opened", "reprint",
        "employee_purchase",
    ]

    print(f"Emulator targeting {args.url}")
    print(f"Interval: {args.interval}s, Fraud rate: {args.fraud_rate*100:.0f}%")
    print(f"Stores: {[s['cin'] for s in STORES]}")
    print()

    with httpx.Client(timeout=10) as client:
        while True:
            store = random.choice(STORES)
            fraud = None
            if random.random() < args.fraud_rate:
                fraud = random.choice(fraud_types)
            try:
                generate_transaction(client, args.url, store, fraud)
            except Exception as e:
                print(f"  ERROR: {e}")
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Test emulator against running backend**

Terminal 1:
```bash
cd poc && python -m backend.main
```

Terminal 2:
```bash
cd poc && python -m emulator.nukkad_emulator --interval 2 --fraud-rate 0.2
```

Expected: emulator prints transaction lines, backend receives them. Hit `http://localhost:8001/api/transactions` — should see transactions accumulating. Hit `http://localhost:8001/api/alerts` — should see fraud alerts.

- [ ] **Step 4: Commit**

```bash
git add poc/emulator/
git commit -m "feat(poc): add Nukkad event emulator with 9 fraud scenarios"
```

---

### Task 10: CV signal consumer + correlator

**Files:**
- Create: `poc/backend/cv_consumer.py`
- Create: `poc/backend/correlator.py`
- Create: `poc/tests/test_correlator.py`

- [ ] **Step 1: Implement cv_consumer.py**

```python
# poc/backend/cv_consumer.py
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from backend.models import CVWindow
import redis.asyncio as aioredis


class CVConsumer:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self.redis = None
        # Per-zone windows: zone_id -> list of CVWindow sorted by time
        self.windows: dict[str, list[CVWindow]] = defaultdict(list)
        # Current accumulator per zone for the active 30s window
        self._accum: dict[str, dict] = {}
        self._window_duration = 30  # seconds
        # Latest signal per camera (for stream viewer)
        self.latest: dict[str, dict] = {}

    async def connect(self):
        self.redis = aioredis.from_url(self.redis_url)

    async def run(self):
        if not self.redis:
            await self.connect()
        pubsub = self.redis.pubsub()
        await pubsub.psubscribe("cv:*")
        async for message in pubsub.listen():
            if message["type"] != "pmessage":
                continue
            try:
                signal = json.loads(message["data"])
                self._process_signal(signal)
            except (json.JSONDecodeError, KeyError):
                continue

    def _process_signal(self, signal: dict):
        camera_id = signal.get("camera_id", "")
        self.latest[camera_id] = signal

        ts = datetime.fromisoformat(signal["ts"].replace("Z", "+00:00"))
        non_seller = signal.get("non_seller_present", False)
        non_seller_count = signal.get("non_seller_count", 0)

        for zone_data in signal.get("zones", []):
            zone_id = zone_data["pos_zone"]
            key = f"{camera_id}:{zone_id}"

            if key not in self._accum:
                window_start = ts.replace(second=(ts.second // self._window_duration) * self._window_duration, microsecond=0)
                self._accum[key] = {
                    "zone_id": zone_id,
                    "camera_id": camera_id,
                    "window_start": window_start,
                    "seller_frames": 0,
                    "non_seller_frames": 0,
                    "non_seller_max": 0,
                    "bill_motion": False,
                    "bill_bg": False,
                    "frame_count": 0,
                }

            acc = self._accum[key]
            window_end = acc["window_start"] + timedelta(seconds=self._window_duration)

            if ts >= window_end:
                # Close current window
                self._close_window(key, acc)
                # Start new window
                new_start = ts.replace(second=(ts.second // self._window_duration) * self._window_duration, microsecond=0)
                self._accum[key] = {
                    "zone_id": zone_id,
                    "camera_id": camera_id,
                    "window_start": new_start,
                    "seller_frames": 0,
                    "non_seller_frames": 0,
                    "non_seller_max": 0,
                    "bill_motion": False,
                    "bill_bg": False,
                    "frame_count": 0,
                }
                acc = self._accum[key]

            acc["frame_count"] += 1
            if zone_data.get("seller", False):
                acc["seller_frames"] += 1
            if non_seller:
                acc["non_seller_frames"] += 1
            acc["non_seller_max"] = max(acc["non_seller_max"], non_seller_count)
            if zone_data.get("bill_motion", False):
                acc["bill_motion"] = True
            if zone_data.get("bill_bg", False):
                acc["bill_bg"] = True

    def _close_window(self, key: str, acc: dict):
        fc = max(acc["frame_count"], 1)
        window = CVWindow(
            pos_zone=acc["zone_id"],
            camera_id=acc["camera_id"],
            window_start=acc["window_start"],
            window_end=acc["window_start"] + timedelta(seconds=self._window_duration),
            seller_present_pct=acc["seller_frames"] / fc,
            non_seller_present_pct=acc["non_seller_frames"] / fc,
            non_seller_count_max=acc["non_seller_max"],
            bill_motion_detected=acc["bill_motion"],
            bill_bg_change_detected=acc["bill_bg"],
            frame_count=acc["frame_count"],
        )
        self.windows[acc["zone_id"]].append(window)

        # Prune old windows (keep 14 days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        self.windows[acc["zone_id"]] = [
            w for w in self.windows[acc["zone_id"]] if w.window_end > cutoff
        ]

    def get_windows(self, pos_zone: str, start_ts: datetime, end_ts: datetime) -> list[CVWindow]:
        """Get all windows overlapping the given time range for a POS zone."""
        result = []
        for w in self.windows.get(pos_zone, []):
            if w.window_end > start_ts and w.window_start < end_ts:
                result.append(w)
        return result
```

- [ ] **Step 2: Implement correlator.py**

```python
# poc/backend/correlator.py
from datetime import datetime, timezone, timedelta
from backend.models import TransactionSession, CVWindow
from backend.cv_consumer import CVConsumer
from backend.config import Config


def correlate(txn: TransactionSession, cv_consumer: CVConsumer, config: Config) -> TransactionSession:
    """Attach CV data to a committed transaction."""
    # Resolve camera for this transaction's POS
    seller_window_id = f"{txn.store_id}_{txn.pos_terminal}"
    camera = config.get_camera_by_seller_window(seller_window_id)

    if not camera:
        txn.cv_confidence = "UNMAPPED"
        return txn

    # Find the POS zone
    if not camera.pos_zones:
        txn.cv_confidence = "UNMAPPED"
        return txn

    pos_zone = camera.pos_zones[0].zone_id  # primary zone

    # Parse transaction timestamps
    try:
        start = datetime.fromisoformat(txn.started_at.replace("Z", "+00:00")) if txn.started_at else None
        end = txn.committed_at
    except (ValueError, AttributeError):
        txn.cv_confidence = "UNAVAILABLE"
        return txn

    if not start or not end:
        txn.cv_confidence = "UNAVAILABLE"
        return txn

    # Widen window by +-3s for clock skew tolerance
    start_padded = start - timedelta(seconds=3)
    end_padded = end + timedelta(seconds=3)

    windows = cv_consumer.get_windows(pos_zone, start_padded, end_padded)

    if not windows:
        txn.cv_confidence = "UNAVAILABLE"
        return txn

    # Aggregate across matching windows
    total_frames = sum(w.frame_count for w in windows)
    if total_frames == 0:
        txn.cv_confidence = "UNAVAILABLE"
        return txn

    seller_pct = sum(w.seller_present_pct * w.frame_count for w in windows) / total_frames
    non_seller_pct = sum(w.non_seller_present_pct * w.frame_count for w in windows) / total_frames
    bill_motion = any(w.bill_motion_detected for w in windows)
    bill_bg = any(w.bill_bg_change_detected for w in windows)
    max_non_seller = max(w.non_seller_count_max for w in windows)

    txn.cv_non_seller_present = non_seller_pct > 0.3
    txn.cv_non_seller_count = max_non_seller
    txn.cv_receipt_detected = bill_motion or bill_bg
    txn.cv_confidence = "REDUCED" if camera.multi_pos else "HIGH"
    txn.camera_id = camera.camera_id
    txn.device_id = camera.xprotect_device_id

    return txn
```

- [ ] **Step 3: Write correlator test**

```python
# poc/tests/test_correlator.py
from datetime import datetime, timezone, timedelta
from backend.correlator import correlate
from backend.cv_consumer import CVConsumer
from backend.config import Config
from backend.models import TransactionSession, CVWindow


def test_correlate_with_cv_data():
    cv = CVConsumer.__new__(CVConsumer)
    cv.windows = {}
    cv.latest = {}
    cv._accum = {}

    now = datetime.now(timezone.utc)
    window = CVWindow(
        pos_zone="POS3",
        camera_id="cam-rambandi-01",
        window_start=now - timedelta(seconds=30),
        window_end=now,
        seller_present_pct=0.9,
        non_seller_present_pct=0.8,
        non_seller_count_max=2,
        bill_motion_detected=True,
        bill_bg_change_detected=False,
        frame_count=180,
    )
    cv.windows["POS3"] = [window]

    config = Config.__new__(Config)
    from backend.config import CameraEntry, PosZoneConfig
    config.cameras = [
        CameraEntry(
            seller_window_id="NDCIN1223_POS 3",
            store_id="NDCIN1223",
            pos_terminal="POS 3",
            camera_id="cam-rambandi-01",
            rtsp_url="",
            xprotect_device_id="",
            multi_pos=False,
            pos_zones=[PosZoneConfig(zone_id="POS3", seller_zone=[], bill_zone=[])],
        )
    ]

    txn = TransactionSession(
        id="TXN-001",
        store_id="NDCIN1223",
        pos_terminal="POS 3",
        source="push_assembled",
        started_at=(now - timedelta(seconds=25)).isoformat(),
        committed_at=now,
    )

    result = correlate(txn, cv, config)
    assert result.cv_non_seller_present is True
    assert result.cv_receipt_detected is True
    assert result.cv_confidence == "HIGH"
    assert result.camera_id == "cam-rambandi-01"


def test_correlate_no_camera():
    cv = CVConsumer.__new__(CVConsumer)
    cv.windows = {}
    cv.latest = {}
    cv._accum = {}

    config = Config.__new__(Config)
    config.cameras = []

    txn = TransactionSession(
        id="TXN-002",
        store_id="UNKNOWN",
        pos_terminal="POS 1",
        source="push_assembled",
    )

    result = correlate(txn, cv, config)
    assert result.cv_confidence == "UNMAPPED"
```

- [ ] **Step 4: Run tests**

```bash
cd poc && python -m pytest tests/test_correlator.py -v
```

- [ ] **Step 5: Wire cv_consumer into main.py**

Add to `poc/backend/main.py` — import cv_consumer, start it as a background task in lifespan, and use correlator in the receiver:

In the imports section of main.py, add:
```python
from backend.cv_consumer import CVConsumer
from backend.correlator import correlate
```

Add to globals:
```python
cv_consumer = CVConsumer()
```

In the lifespan, add before `yield`:
```python
tasks.append(asyncio.create_task(cv_consumer.run()))
```

In `receiver.py`, after `txn = assembler.commit(payload)`, before fraud engine:
```python
from backend.main import cv_consumer, config
from backend.correlator import correlate
# ... in the CommitTransaction handler:
txn = correlate(txn, cv_consumer, config)
```

- [ ] **Step 6: Commit**

```bash
git add poc/backend/cv_consumer.py poc/backend/correlator.py poc/tests/test_correlator.py
git commit -m "feat(poc): add CV signal consumer + correlator with 30s window aggregation"
```

---

### Task 11: Event timeline builder

**Files:**
- Create: `poc/backend/timeline.py`

- [ ] **Step 1: Implement timeline.py**

```python
# poc/backend/timeline.py
from backend.models import TransactionSession, TimelineEvent, CVWindow
from backend.cv_consumer import CVConsumer
from backend.config import Config
from datetime import datetime, timezone, timedelta


def build_timeline(txn: TransactionSession, cv_consumer: CVConsumer = None, config: Config = None) -> list[dict]:
    """Build a unified event timeline for a transaction, merging POS + CV events."""
    events: list[dict] = []

    # POS events
    if txn.started_at:
        events.append({"ts": txn.started_at, "source": "pos", "type": "begin_transaction",
                        "data": {"cashier": txn.cashier_id, "transaction_type": txn.transaction_type}})

    for item in txn.items:
        events.append({"ts": item.line_timestamp or txn.started_at, "source": "pos", "type": "sale_line",
                        "data": {"item": item.item_description, "qty": item.item_quantity,
                                 "amount": item.total_amount, "scan": item.scan_attribute,
                                 "attribute": item.item_attribute, "discount_type": item.discount_type,
                                 "discount": item.discount}})

    for pay in txn.payments:
        events.append({"ts": pay.line_timestamp or "", "source": "pos", "type": "payment_line",
                        "data": {"mode": pay.line_attribute, "amount": pay.amount}})

    for total in txn.totals:
        events.append({"ts": "", "source": "pos", "type": "total_line",
                        "data": {"type": total.line_attribute, "amount": total.amount}})

    if txn.committed_at:
        events.append({"ts": txn.committed_at.isoformat() if isinstance(txn.committed_at, datetime) else str(txn.committed_at),
                        "source": "pos", "type": "commit",
                        "data": {"bill_number": txn.bill_number}})

    for ev in txn.events:
        events.append({"ts": ev.line_timestamp or "", "source": "pos", "type": "transaction_event",
                        "data": {"attribute": ev.line_attribute, "description": ev.event_description}})

    # CV events (if available)
    if txn.cv_confidence and txn.cv_confidence not in ("UNAVAILABLE", "UNMAPPED", ""):
        if txn.cv_non_seller_present is not None:
            events.append({"ts": txn.started_at or "", "source": "cv", "type": "customer_presence",
                            "data": {"present": txn.cv_non_seller_present, "count": txn.cv_non_seller_count}})
        if txn.cv_receipt_detected is not None:
            ts = txn.committed_at.isoformat() if isinstance(txn.committed_at, datetime) else str(txn.committed_at) if txn.committed_at else ""
            events.append({"ts": ts, "source": "cv", "type": "receipt_detection",
                            "data": {"detected": txn.cv_receipt_detected}})

    # Sort by timestamp
    events.sort(key=lambda e: e.get("ts", "") or "")

    return events
```

- [ ] **Step 2: Add timeline endpoint to main.py**

Add to main.py:
```python
from backend.timeline import build_timeline

@app.get("/api/transactions/{txn_id}/timeline")
async def get_timeline(txn_id: str):
    for txn_data in storage.read("transactions"):
        if txn_data.get("id") == txn_id:
            from backend.models import TransactionSession
            txn = TransactionSession(**txn_data)
            return build_timeline(txn)
    return []
```

- [ ] **Step 3: Commit**

```bash
git add poc/backend/timeline.py
git commit -m "feat(poc): add event timeline builder (unified POS + CV events)"
```

---

### Task 12: CV signal emulator

**Files:**
- Create: `poc/emulator/cv_emulator.py`

- [ ] **Step 1: Implement cv_emulator.py**

```python
# poc/emulator/cv_emulator.py
"""
CV signal emulator.
Publishes fake CV signals to Redis, simulating edge device output.

Usage:
    python -m emulator.cv_emulator [--redis redis://localhost:6379] [--fps 6]
"""
import argparse
import json
import time
import random
from datetime import datetime, timezone
import redis


CAMERAS = [
    {"store_id": "NDCIN1223", "camera_id": "cam-rambandi-01", "zones": [{"pos_zone": "POS3"}]},
    {"store_id": "NSCIN8227", "camera_id": "cam-encalm-01", "zones": [{"pos_zone": "POS4"}]},
    {"store_id": "NDCIN1227", "camera_id": "cam-kfc-01", "zones": [{"pos_zone": "POS1"}]},
    {"store_id": "NDCIN1226", "camera_id": "cam-pizzahut-01", "zones": [{"pos_zone": "POS2"}]},
    {"store_id": "NDCIN1228", "camera_id": "cam-haldirams-01", "zones": [{"pos_zone": "POS1"}]},
]


def generate_signal(camera: dict) -> dict:
    """Generate a single CV signal frame."""
    # Simulate: 80% of time seller is present, 60% customer present
    zones = []
    for zone in camera["zones"]:
        zones.append({
            "pos_zone": zone["pos_zone"],
            "seller": random.random() < 0.8,
            "bill_motion": random.random() < 0.05,  # 5% of frames
            "bill_bg": random.random() < 0.03,       # 3% of frames
        })

    non_seller_count = random.choice([0, 0, 0, 1, 1, 2])  # weighted toward 0-1
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "camera_id": camera["camera_id"],
        "zones": zones,
        "non_seller_count": non_seller_count,
        "non_seller_present": non_seller_count > 0,
    }


def main():
    parser = argparse.ArgumentParser(description="CV Signal Emulator")
    parser.add_argument("--redis", default="redis://localhost:6379", help="Redis URL")
    parser.add_argument("--fps", type=int, default=6, help="Frames per second per camera")
    args = parser.parse_args()

    r = redis.from_url(args.redis)
    interval = 1.0 / args.fps

    print(f"CV Emulator: {len(CAMERAS)} cameras at {args.fps} FPS each")
    print(f"Redis: {args.redis}")
    print()

    frame_count = 0
    while True:
        for camera in CAMERAS:
            signal = generate_signal(camera)
            channel = f"cv:{camera['store_id']}:{camera['camera_id']}"
            r.publish(channel, json.dumps(signal))
        frame_count += 1
        if frame_count % (args.fps * 10) == 0:  # log every 10 seconds
            print(f"  Published {frame_count} frames per camera")
        time.sleep(interval)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test end-to-end with all emulators**

Terminal 1: `redis-server --daemonize yes`
Terminal 2: `cd poc && python -m backend.main`
Terminal 3: `cd poc && python -m emulator.cv_emulator --fps 2`
Terminal 4: `cd poc && python -m emulator.nukkad_emulator --interval 3`

Check:
- `http://localhost:8001/api/transactions` — transactions with CV correlation data
- `http://localhost:8001/api/alerts` — fraud alerts
- `http://localhost:8001/api/transactions/{id}/timeline` — merged event timeline

- [ ] **Step 3: Commit**

```bash
git add poc/emulator/cv_emulator.py
git commit -m "feat(poc): add CV signal emulator publishing to Redis"
```

---

## Plan complete

This plan produces:
- **Backend** — fully functional FastAPI app with push event receiver, transaction assembler, CV signal consumer, correlator, 29-rule fraud engine, event timeline, JSONL storage, WebSocket broadcaster
- **Emulator** — Nukkad event generator (9 fraud scenarios) + CV signal generator, both producing realistic data
- **Config** — stores, camera mapping, rule config
- **Tests** — unit tests for models, storage, assembler, fraud engine, correlator, receiver

The backend is **production code** — same code runs in POC and production. Only transport (Redis → MQTT) and storage (JSONL → PostgreSQL) change later.

Next plans needed:
1. **CV Service** — YOLO + zones + motion + rolling buffer + Redis publisher
2. **Dashboard** — React app with 7 pages including zone drawer and video player
3. **Camera API** — store/camera/zone CRUD endpoints + frame grab
