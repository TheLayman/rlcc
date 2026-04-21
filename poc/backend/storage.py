from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


class Storage:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "events").mkdir(exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._seen: set[str] = set()
        self._recent_pos_events: deque[dict] = deque(maxlen=200)

    def _lock_for(self, name: str) -> threading.Lock:
        return self._locks.setdefault(name, threading.Lock())

    def _filepath(self, name: str) -> Path:
        return self.data_dir / f"{name}.jsonl"

    def append(self, name: str, record: dict):
        with self._lock_for(name):
            with open(self._filepath(name), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")

    def read(self, name: str) -> list[dict]:
        path = self._filepath(name)
        if not path.exists():
            return []
        with self._lock_for(name):
            records = []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return records

    def update(self, name: str, record_id: str, updates: dict):
        with self._lock_for(name):
            path = self._filepath(name)
            if not path.exists():
                return
            records = []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            for record in records:
                if record.get("id") == record_id:
                    record.update(updates)
                    break
            with open(path, "w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record, default=str) + "\n")

    def replace(self, name: str, records: list[dict]):
        with self._lock_for(name):
            with open(self._filepath(name), "w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record, default=str) + "\n")

    def append_event(self, event: dict):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.data_dir / "events" / f"{today}.jsonl"
        with self._lock_for("events"):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")

        self._recent_pos_events.appendleft(event)

    def read_events(self, date: str | None = None) -> list[dict]:
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.data_dir / "events" / f"{date}.jsonl"
        if not path.exists():
            return []
        with self._lock_for("events"):
            records = []
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return records

    def get_recent_pos_events(self) -> list[dict]:
        return list(self._recent_pos_events)

    def _dedup_key(self, event: dict) -> str:
        return "|".join(
            [
                str(event.get("transactionSessionId", "")),
                str(event.get("event", "")),
                str(event.get("lineNumber", "")),
                str(event.get("lineTimeStamp", "")),
                str(event.get("transactionNumber", "")),
                str(event.get("lineAttribute", "")),
            ]
        )

    def is_duplicate(self, event: dict) -> bool:
        return self._dedup_key(event) in self._seen

    def mark_seen(self, event: dict):
        self._seen.add(self._dedup_key(event))
