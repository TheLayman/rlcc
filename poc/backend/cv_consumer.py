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
        self.windows: dict[str, list[CVWindow]] = defaultdict(list)
        self._accum: dict[str, dict] = {}
        self._window_duration = 30
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
                    "zone_id": zone_id, "camera_id": camera_id,
                    "window_start": window_start,
                    "seller_frames": 0, "non_seller_frames": 0,
                    "non_seller_max": 0, "bill_motion": False, "bill_bg": False, "frame_count": 0,
                }

            acc = self._accum[key]
            window_end = acc["window_start"] + timedelta(seconds=self._window_duration)

            if ts >= window_end:
                self._close_window(key, acc)
                new_start = ts.replace(second=(ts.second // self._window_duration) * self._window_duration, microsecond=0)
                self._accum[key] = {
                    "zone_id": zone_id, "camera_id": camera_id,
                    "window_start": new_start,
                    "seller_frames": 0, "non_seller_frames": 0,
                    "non_seller_max": 0, "bill_motion": False, "bill_bg": False, "frame_count": 0,
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
            pos_zone=acc["zone_id"], camera_id=acc["camera_id"],
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
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        self.windows[acc["zone_id"]] = [w for w in self.windows[acc["zone_id"]] if w.window_end > cutoff]

    def get_windows(self, pos_zone: str, start_ts: datetime, end_ts: datetime) -> list[CVWindow]:
        return [w for w in self.windows.get(pos_zone, []) if w.window_end > start_ts and w.window_start < end_ts]
