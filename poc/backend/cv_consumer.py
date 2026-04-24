from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis

from backend.models import CVWindow


@dataclass
class ActivityState:
    store_id: str
    camera_id: str
    pos_zone: str
    started_at: datetime
    last_seen: datetime
    active: bool = True
    alert_emitted: bool = False
    non_seller_count_max: int = 0


class CVConsumer:
    def __init__(self, redis_url: str = "redis://localhost:6379", history_size: int = 200):
        self.redis_url = redis_url
        self.redis = None
        self.windows: dict[str, list[CVWindow]] = defaultdict(list)
        self._accum: dict[str, dict] = {}
        self._window_duration = 30
        self.latest: dict[str, dict] = {}
        self.recent_signals: deque[dict] = deque(maxlen=history_size)
        self.recent_activity: deque[dict] = deque(maxlen=history_size)
        self.latest_activity: dict[str, dict] = {}
        self.activity_states: dict[str, ActivityState] = {}
        self.last_signal_at: datetime | None = None
        self.last_activity_at: datetime | None = None

    @staticmethod
    def window_key(camera_id: str, pos_zone: str) -> str:
        return f"{camera_id}:{pos_zone}"

    async def connect(self):
        self.redis = aioredis.from_url(self.redis_url)

    async def run(self):
        while True:
            try:
                if not self.redis:
                    await self.connect()
                pubsub = self.redis.pubsub()
                await pubsub.psubscribe("cv:*", "activity:*")
                async for message in pubsub.listen():
                    if message["type"] != "pmessage":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        channel = message["channel"].decode("utf-8") if isinstance(message["channel"], bytes) else str(message["channel"])
                        if channel.startswith("activity:"):
                            self._process_activity(payload, channel)
                        else:
                            self._process_signal(payload, channel)
                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue
            except Exception as e:
                print(f"[cv_consumer] Redis error: {e}, reconnecting in 5s...")
                self.redis = None
                await asyncio.sleep(5)

    def _process_activity(self, payload: dict, channel: str):
        parts = channel.split(":")
        if len(parts) >= 3 and not payload.get("store_id"):
            payload["store_id"] = parts[1]
        camera_id = payload.get("camera_id", "")
        zone_id = payload.get("pos_zone", "")
        key = f"{camera_id}:{zone_id}" if zone_id else camera_id
        self.latest_activity[key] = payload
        self.recent_activity.appendleft(payload)
        self.last_activity_at = datetime.now(timezone.utc)

    def _process_signal(self, signal: dict, channel: str):
        store_id = ""
        parts = channel.split(":")
        if len(parts) >= 3:
            store_id = parts[1]

        camera_id = signal.get("camera_id", "")
        signal["store_id"] = store_id or signal.get("store_id", "")
        self.latest[camera_id] = signal
        self.recent_signals.appendleft(signal)
        self.last_signal_at = datetime.now(timezone.utc)

        ts = datetime.fromisoformat(signal["ts"].replace("Z", "+00:00"))
        non_seller = bool(signal.get("non_seller_present", False))
        non_seller_count = int(signal.get("non_seller_count", 0) or 0)

        for zone_data in signal.get("zones", []):
            zone_id = zone_data["pos_zone"]
            key = self.window_key(camera_id, zone_id)

            if key not in self._accum:
                window_start = ts.replace(second=(ts.second // self._window_duration) * self._window_duration, microsecond=0)
                self._accum[key] = {
                    "zone_id": zone_id,
                    "camera_id": camera_id,
                    "store_id": signal["store_id"],
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
                self._close_window(key, acc)
                new_start = ts.replace(second=(ts.second // self._window_duration) * self._window_duration, microsecond=0)
                self._accum[key] = {
                    "zone_id": zone_id,
                    "camera_id": camera_id,
                    "store_id": signal["store_id"],
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

            self._update_activity_state(
                store_id=signal["store_id"],
                camera_id=camera_id,
                pos_zone=zone_id,
                ts=ts,
                seller_present=bool(zone_data.get("seller", False)),
                non_seller_present=non_seller,
                non_seller_count=non_seller_count,
            )

    def _update_activity_state(
        self,
        *,
        store_id: str,
        camera_id: str,
        pos_zone: str,
        ts: datetime,
        seller_present: bool,
        non_seller_present: bool,
        non_seller_count: int,
    ):
        key = f"{camera_id}:{pos_zone}"
        state = self.activity_states.get(key)
        active = seller_present and non_seller_present
        if active:
            if state and state.active:
                state.last_seen = ts
                state.non_seller_count_max = max(state.non_seller_count_max, non_seller_count)
            else:
                self.activity_states[key] = ActivityState(
                    store_id=store_id,
                    camera_id=camera_id,
                    pos_zone=pos_zone,
                    started_at=ts,
                    last_seen=ts,
                    non_seller_count_max=non_seller_count,
                )
        elif state and state.active:
            state.active = False
            state.last_seen = ts

    def _close_window(self, key: str, acc: dict):
        frame_count = max(acc["frame_count"], 1)
        window = CVWindow(
            pos_zone=acc["zone_id"],
            camera_id=acc["camera_id"],
            store_id=acc["store_id"],
            window_start=acc["window_start"],
            window_end=acc["window_start"] + timedelta(seconds=self._window_duration),
            seller_present_pct=acc["seller_frames"] / frame_count,
            non_seller_present_pct=acc["non_seller_frames"] / frame_count,
            non_seller_count_max=acc["non_seller_max"],
            bill_motion_detected=acc["bill_motion"],
            bill_bg_change_detected=acc["bill_bg"],
            frame_count=acc["frame_count"],
        )
        self.windows[key].append(window)
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        self.windows[key] = [w for w in self.windows[key] if w.window_end > cutoff]

    def get_windows(self, camera_id: str, pos_zone: str, start_ts: datetime, end_ts: datetime) -> list[CVWindow]:
        key = self.window_key(camera_id, pos_zone)
        return [w for w in self.windows.get(key, []) if w.window_end > start_ts and w.window_start < end_ts]

    def get_recent_signals(self) -> list[dict]:
        return list(self.recent_signals)

    def get_recent_activity(self) -> list[dict]:
        return list(self.recent_activity)

    def get_latest_activity(self) -> dict[str, dict]:
        return dict(self.latest_activity)

    def get_health(self) -> dict:
        return {
            "redis_url": self.redis_url,
            "connected": self.redis is not None,
            "last_signal_at": self.last_signal_at.isoformat() if self.last_signal_at else None,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "camera_count": len(self.latest),
            "active_copresence_sessions": sum(1 for state in self.activity_states.values() if state.active),
            "recent_activity_count": len(self.recent_activity),
        }

    def prune_inactive_states(self, stale_after_seconds: int = 15):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        for key, state in list(self.activity_states.items()):
            if state.last_seen < cutoff and not state.active:
                del self.activity_states[key]
