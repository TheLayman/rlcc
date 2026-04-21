"""
CV signal emulator — publishes fake CV signals to Redis.

Usage:
    python3 -m emulator.cv_emulator [--redis redis://localhost:6379] [--fps 6]
"""
import argparse
import json
import time
import random
from datetime import datetime, timezone
import redis


CAMERAS = [
    {"store_id": "NDCIN1223", "camera_id": "cam-rambandi-01", "zones": [{"pos_zone": "POS3"}]},
    {"store_id": "NDCIN1231", "camera_id": "cam-nizami-01", "zones": [{"pos_zone": "POS1"}]},
    {"store_id": "NDCIN1227", "camera_id": "cam-kfc-01", "zones": [{"pos_zone": "POS1"}]},
    {"store_id": "NDCIN1228", "camera_id": "cam-haldirams-01", "zones": [{"pos_zone": "POS1"}]},
]


def generate_signal(camera: dict) -> dict:
    zones = []
    for zone in camera["zones"]:
        zones.append({
            "pos_zone": zone["pos_zone"],
            "seller": random.random() < 0.8,
            "bill_motion": random.random() < 0.05,
            "bill_bg": random.random() < 0.03,
        })
    non_seller_count = random.choice([0, 0, 0, 1, 1, 2])
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
        if frame_count % (args.fps * 10) == 0:
            print(f"  Published {frame_count} frames per camera")
        time.sleep(interval)


if __name__ == "__main__":
    main()
