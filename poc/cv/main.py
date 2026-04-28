from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import redis
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from backend.config import CameraEntry, Config, PosZoneConfig, ZONE_POLYGON_FIELDS, get_zone_polygon_value
from backend.settings import get_settings
from cv.activity import ACTIVITY_IDLE, SellerActivityClassifier

try:
    import cv2
except ImportError:  # pragma: no cover - runtime dependency
    cv2 = None

try:
    import torch
except ImportError:  # pragma: no cover - runtime dependency
    torch = None

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover - runtime dependency
    YOLO = None


POC_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = POC_DIR / "config"
DATA_DIR = POC_DIR / "data"
settings = get_settings(POC_DIR / ".env")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _polygon_bbox(polygon: list[list[int]]) -> tuple[int, int, int, int]:
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return min(xs), min(ys), max(xs), max(ys)


ZONE_ANNOTATION_META: dict[str, tuple[str, tuple[int, int, int], bool]] = {
    "seller_zone": ("SELLER", (0, 255, 0), False),
    "customer_zone": ("CUSTOMER", (255, 170, 0), False),
    "midline": ("MIDLINE", (255, 255, 255), True),
    "pos_zone": ("POS", (90, 90, 255), False),
    "pos_screen_zone": ("POS SCREEN", (255, 120, 0), False),
    "bill_zone": ("BILL", (255, 200, 0), False),
}


def _zone_payload(zone: PosZoneConfig) -> dict[str, Any]:
    return {
        "zone_id": zone.zone_id,
        **{field_name: getattr(zone, field_name) for field_name in ZONE_POLYGON_FIELDS},
    }


@dataclass
class CameraState:
    camera: CameraEntry
    latest_frame: bytes = b""
    latest_signal: dict[str, Any] = field(default_factory=dict)
    last_frame_at: str | None = None
    last_error: str | None = None
    source_mode: str = "placeholder"
    recorder: subprocess.Popen | None = None
    running: bool = False
    frame_count: int = 0
    last_seller_at: dict[str, datetime] = field(default_factory=dict)
    last_non_seller_at: datetime | None = None
    zone_copresence_start: dict[str, datetime] = field(default_factory=dict)
    bill_prev_frames: dict[str, np.ndarray | None] = field(default_factory=dict)
    bill_baselines: dict[str, np.ndarray | None] = field(default_factory=dict)
    activity_last_published_at: dict[str, datetime] = field(default_factory=dict)
    activity_last_label: dict[str, str] = field(default_factory=dict)
    fps_samples: deque = field(default_factory=lambda: deque(maxlen=20))
    current_fps: float = 0.0


class CVRuntime:
    def __init__(self, config: Config, redis_url: str, buffer_root: Path):
        self.config = config
        self.redis = redis.from_url(redis_url)
        self.buffer_root = buffer_root
        self.buffer_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.states: dict[str, CameraState] = self._build_states()
        self.threads: list[threading.Thread] = []
        self.detector = self._load_detector()
        self.detector_name = self.detector.__class__.__name__ if self.detector is not None else "disabled"
        self.activity_classifier = SellerActivityClassifier()

    def _rule(self, key: str, default: float) -> float:
        value = self.config.rules.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def _build_states(self) -> dict[str, CameraState]:
        return {
            camera.camera_id: CameraState(camera=camera) for camera in self.config.cameras if camera.enabled
        }

    def _load_detector(self):
        if YOLO is None or cv2 is None:
            return None
        if os.getenv("CV_ENABLE_DETECTOR", "1") == "0":
            return None
        model_name = os.getenv("YOLO_MODEL_PATH", "").strip()
        force_cpu = os.getenv("CV_FORCE_CPU", "0").strip().lower() in {"1", "true", "yes", "on"}
        use_gpu = torch is not None and torch.cuda.is_available() and not force_cpu
        if not model_name:
            model_name = "yolov8m.pt" if use_gpu else "yolov8s.pt"
        try:
            return YOLO(model_name)
        except Exception:
            return None

    def start(self):
        for camera_id in self.states:
            thread = threading.Thread(target=self._camera_worker, args=(camera_id,), daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        with self.lock:
            states = list(self.states.values())
        for state in states:
            if state.recorder and state.recorder.poll() is None:
                state.recorder.terminate()
        for thread in self.threads:
            thread.join(timeout=1)
        self.threads = []

    def reload(self):
        self.stop()
        self.config.reload()
        self.stop_event = threading.Event()
        with self.lock:
            self.states = self._build_states()
        self.start()

    def cameras(self) -> list[dict]:
        with self.lock:
            target_fps = self._rule("cv_target_fps", 5.0)
            return [
                {
                    "camera_id": state.camera.camera_id,
                    "store_id": state.camera.store_id,
                    "pos_terminal_no": state.camera.pos_terminal_no,
                    "display_pos_label": state.camera.display_pos_label,
                    "rtsp_url": state.camera.rtsp_url,
                    "source_mode": state.source_mode,
                    "last_frame_at": state.last_frame_at,
                    "last_error": state.last_error,
                    "current_fps": state.current_fps,
                    "target_fps": target_fps,
                    "frame_count": state.frame_count,
                    "fps_starved": state.current_fps > 0 and state.current_fps < target_fps * 0.6,
                }
                for state in self.states.values()
            ]

    def get_state(self, camera_id: str | None = None) -> CameraState:
        with self.lock:
            if camera_id and camera_id in self.states:
                return self.states[camera_id]
            if self.states:
                return next(iter(self.states.values()))
        raise HTTPException(status_code=404, detail="Camera not found")

    def _camera_worker(self, camera_id: str):
        state = self.states[camera_id]
        camera = state.camera
        capture = None
        last_frame_monotonic: float | None = None

        if cv2 is None:
            state.last_error = "opencv-python-headless is not installed"

        if cv2 is not None and camera.rtsp_url:
            capture = cv2.VideoCapture(camera.rtsp_url)
            if capture.isOpened():
                state.source_mode = "rtsp"
                state.recorder = self._start_recorder(camera)
            else:
                state.last_error = "Could not open RTSP stream"
        else:
            state.last_error = "RTSP URL not configured"

        while not self.stop_event.is_set():
            loop_started = time.monotonic()
            target_fps = max(self._rule("cv_target_fps", 5.0), 1.0)
            target_interval = 1.0 / target_fps

            frame = None
            if capture is not None and capture.isOpened():
                ok, raw = capture.read()
                if ok and raw is not None:
                    frame = raw
                    if state.source_mode != "rtsp":
                        state.source_mode = "rtsp"
                    if state.last_error in ("RTSP frame read failed", "Could not open RTSP stream"):
                        state.last_error = None
                else:
                    state.last_error = "RTSP frame read failed"

            if frame is None:
                frame = self._placeholder_frame(camera, state.last_error or "Waiting for live stream")
                state.source_mode = "placeholder"

            people = self._detect_people(frame)
            signal = self._build_signal(camera, state, frame, people)
            self._maybe_publish_activity(camera, state, frame, people, signal)
            annotated = self._annotate(frame.copy(), camera, signal, people)
            encoded = self._encode_frame(annotated)

            now_monotonic = time.monotonic()
            if last_frame_monotonic is not None:
                delta = now_monotonic - last_frame_monotonic
                if delta > 0:
                    state.fps_samples.append(1.0 / delta)
                    state.current_fps = round(
                        sum(state.fps_samples) / len(state.fps_samples), 2
                    )
            last_frame_monotonic = now_monotonic

            with self.lock:
                state.latest_frame = encoded
                state.latest_signal = signal
                state.last_frame_at = iso_now()
                state.running = True
                state.frame_count += 1

            try:
                self.redis.publish(f"cv:{camera.store_id}:{camera.camera_id}", self._json(signal))
                if state.last_error == "Failed to publish CV signal to Redis":
                    state.last_error = None
            except Exception:
                with self.lock:
                    state.last_error = "Failed to publish CV signal to Redis"

            self._prune_buffer(camera.camera_id)

            elapsed = time.monotonic() - loop_started
            sleep_for = max(0.01, target_interval - elapsed)
            time.sleep(sleep_for)

        if capture is not None:
            capture.release()

    def _start_recorder(self, camera: CameraEntry):
        if not camera.rtsp_url:
            return None
        out_dir = self.buffer_root / camera.camera_id
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            camera.rtsp_url,
            "-an",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_format",
            "mpegts",
            "-segment_time",
            "60",
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            (out_dir / "segment_%Y-%m-%dT%H-%M-%S.ts").as_posix(),
        ]
        try:
            env = dict(os.environ)
            env["TZ"] = "UTC"
            return subprocess.Popen(cmd, env=env)
        except Exception:
            return None

    def _prune_buffer(self, camera_id: str):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.video_buffer_minutes)
        camera_dir = self.buffer_root / camera_id
        for segment in list(camera_dir.glob("segment_*.ts")) + list(camera_dir.glob("segment_*.mp4")):
            try:
                stamp = datetime.strptime(segment.stem.replace("segment_", ""), "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if stamp < cutoff:
                segment.unlink(missing_ok=True)

    def _placeholder_frame(self, camera: CameraEntry, message: str) -> np.ndarray:
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        if cv2 is None:
            return frame
        cv2.putText(frame, "RLCC CV DEBUG", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
        cv2.putText(frame, camera.camera_id, (40, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (80, 200, 255), 2)
        cv2.putText(frame, camera.display_pos_label or camera.pos_terminal_no, (40, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 255, 160), 2)
        cv2.putText(frame, message[:80], (40, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 180, 180), 2)
        return frame

    def _detect_people(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        if self.detector is None:
            return []
        try:
            force_cpu = os.getenv("CV_FORCE_CPU", "0").strip().lower() in {"1", "true", "yes", "on"}
            device = 0 if torch is not None and torch.cuda.is_available() and not force_cpu else "cpu"
            results = self.detector.predict(frame, classes=[0], conf=0.35, imgsz=640, verbose=False, device=device)
            boxes: list[tuple[int, int, int, int]] = []
            for box in results[0].boxes:
                x1, y1, x2, y2 = [int(value) for value in box.xyxy[0].tolist()]
                boxes.append((x1, y1, x2, y2))
            return boxes
        except Exception:
            return []

    @staticmethod
    def _feet_point(bbox: tuple[int, int, int, int]) -> tuple[int, int]:
        x1, _y1, x2, y2 = bbox
        return ((x1 + x2) // 2, y2)

    @staticmethod
    def _bbox_overlaps_polygon(
        bbox: tuple[int, int, int, int],
        polygon: np.ndarray,
    ) -> bool:
        if cv2 is None:
            return False
        x1, y1, x2, y2 = bbox
        sample_points = [
            ((x1 + x2) // 2, y2),                 # feet
            ((x1 + x2) // 2, (y1 + y2) // 2),     # center
            (x1, y2),
            (x2, y2),
            ((x1 + x2) // 2, (y2 + (y1 + y2) // 2) // 2),
        ]
        for point in sample_points:
            if cv2.pointPolygonTest(polygon, point, False) >= 0:
                return True
        return False

    def _person_in_zone(
        self,
        bbox: tuple[int, int, int, int],
        polygon_points: list[list[int]],
    ) -> bool:
        if cv2 is None or not polygon_points:
            return False
        polygon = np.array(polygon_points, dtype=np.int32)
        if cv2.pointPolygonTest(polygon, self._feet_point(bbox), False) >= 0:
            return True
        return self._bbox_overlaps_polygon(bbox, polygon)

    def _build_signal(
        self,
        camera: CameraEntry,
        state: CameraState,
        frame: np.ndarray,
        people: list[tuple[int, int, int, int]],
    ) -> dict:
        now = datetime.now(timezone.utc)
        hold_seconds = self._rule("seller_hold_seconds", 3.0)

        seller_zone_map = {
            zone.zone_id: zone.seller_zone
            for zone in camera.pos_zones
            if zone.seller_zone
        }

        observed_non_seller_count = 0
        for bbox in people:
            inside_any_seller = any(
                self._person_in_zone(bbox, poly) for poly in seller_zone_map.values()
            )
            if not inside_any_seller:
                observed_non_seller_count += 1

        if observed_non_seller_count > 0:
            state.last_non_seller_at = now

        zones_payload: list[dict[str, Any]] = []
        for zone in camera.pos_zones:
            raw_seller_present = any(
                self._person_in_zone(bbox, zone.seller_zone) for bbox in people
            ) if zone.seller_zone else False

            if raw_seller_present:
                state.last_seller_at[zone.zone_id] = now

            last_seen = state.last_seller_at.get(zone.zone_id)
            held_seller = False
            if last_seen is not None:
                held_seller = (now - last_seen).total_seconds() <= hold_seconds

            seller_present = bool(raw_seller_present or held_seller)

            bill_motion, bill_bg = self._bill_zone_status(frame, state, zone)

            zones_payload.append(
                {
                    "pos_zone": zone.zone_id,
                    "seller": seller_present,
                    "bill_motion": bill_motion,
                    "bill_bg": bill_bg,
                }
            )

        held_non_seller_count = observed_non_seller_count
        non_seller_held = False
        if observed_non_seller_count == 0 and state.last_non_seller_at is not None:
            if (now - state.last_non_seller_at).total_seconds() <= hold_seconds:
                held_non_seller_count = 1
                non_seller_held = True

        return {
            "ts": iso_now(),
            "store_id": camera.store_id,
            "camera_id": camera.camera_id,
            "zones": zones_payload,
            "non_seller_count": held_non_seller_count,
            "non_seller_present": held_non_seller_count > 0,
            "non_seller_held": non_seller_held,
        }

    def _bill_zone_status(
        self,
        frame: np.ndarray,
        state: CameraState,
        zone: PosZoneConfig,
    ) -> tuple[bool, bool]:
        """Compute bill_motion (pixel-change pct vs previous frame) and
        bill_bg (pixel-change pct vs learned EMA baseline).  The baseline
        pauses updating whenever motion is detected so it learns the idle
        scene only.
        """
        if cv2 is None or not zone.bill_zone:
            return False, False
        x1, y1, x2, y2 = _polygon_bbox(zone.bill_zone)
        x1 = max(x1, 0)
        y1 = max(y1, 0)
        x2 = max(x2, 0)
        y2 = max(y2, 0)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return False, False
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)

        pixel_delta = self._rule("bill_motion_pixel_delta", 20.0)
        motion_threshold = self._rule("bill_motion_pct_threshold", 2.0)
        bg_threshold = self._rule("bill_bg_pct_threshold", 3.0)
        ema_alpha = min(max(self._rule("bill_bg_ema_alpha", 0.05), 0.0), 1.0)

        previous = state.bill_prev_frames.get(zone.zone_id)
        state.bill_prev_frames[zone.zone_id] = gray

        motion_pct = 0.0
        if previous is not None and previous.shape == gray.shape:
            diff = np.abs(gray - previous)
            changed = int(np.count_nonzero(diff > pixel_delta))
            motion_pct = 100.0 * changed / diff.size

        bill_motion = motion_pct > motion_threshold

        baseline = state.bill_baselines.get(zone.zone_id)
        if baseline is None or baseline.shape != gray.shape:
            state.bill_baselines[zone.zone_id] = gray.copy()
            return bill_motion, False

        bg_diff = np.abs(gray - baseline)
        bg_changed = int(np.count_nonzero(bg_diff > pixel_delta))
        bg_pct = 100.0 * bg_changed / bg_diff.size
        bill_bg = bg_pct > bg_threshold

        if not bill_motion:
            state.bill_baselines[zone.zone_id] = (
                (1.0 - ema_alpha) * baseline + ema_alpha * gray
            ).astype(np.float32)

        return bill_motion, bill_bg

    def _maybe_publish_activity(
        self,
        camera: CameraEntry,
        state: CameraState,
        frame: np.ndarray,
        people: list[tuple[int, int, int, int]],
        signal: dict[str, Any],
    ) -> None:
        if not self.activity_classifier.enabled:
            return
        if not signal.get("non_seller_present"):
            for zone in camera.pos_zones:
                state.zone_copresence_start.pop(zone.zone_id, None)
            return

        trigger_seconds = self._rule("activity_trigger_seconds", 15.0)
        activity_fps = max(self._rule("activity_fps", 2.0), 0.5)
        activity_interval = 1.0 / activity_fps
        now = datetime.now(timezone.utc)

        zone_signal_by_id = {entry["pos_zone"]: entry for entry in signal.get("zones", [])}

        for zone in camera.pos_zones:
            zone_signal = zone_signal_by_id.get(zone.zone_id)
            if not zone_signal or not zone_signal.get("seller"):
                state.zone_copresence_start.pop(zone.zone_id, None)
                continue

            started_at = state.zone_copresence_start.get(zone.zone_id)
            if started_at is None:
                state.zone_copresence_start[zone.zone_id] = now
                continue

            dwell = (now - started_at).total_seconds()
            if dwell < trigger_seconds:
                continue

            last_published = state.activity_last_published_at.get(zone.zone_id)
            if last_published is not None and (now - last_published).total_seconds() < activity_interval:
                continue

            seller_bbox = self._select_seller_bbox(zone.seller_zone, people)
            if seller_bbox is None:
                continue

            label, confidence = self.activity_classifier.classify(frame, seller_bbox, zone)
            state.activity_last_published_at[zone.zone_id] = now
            state.activity_last_label[zone.zone_id] = label

            payload = {
                "ts": iso_now(),
                "store_id": camera.store_id,
                "camera_id": camera.camera_id,
                "pos_zone": zone.zone_id,
                "seller_activity": label,
                "confidence": round(float(confidence), 3),
                "copresence_seconds": round(dwell, 1),
            }

            try:
                self.redis.publish(
                    f"activity:{camera.store_id}:{camera.camera_id}", self._json(payload)
                )
            except Exception:
                with self.lock:
                    state.last_error = "Failed to publish CV activity signal to Redis"

    def _select_seller_bbox(
        self,
        seller_zone_points: list[list[int]],
        people: list[tuple[int, int, int, int]],
    ) -> tuple[int, int, int, int] | None:
        if not seller_zone_points:
            return None
        best_bbox: tuple[int, int, int, int] | None = None
        best_area = -1
        for bbox in people:
            if not self._person_in_zone(bbox, seller_zone_points):
                continue
            x1, y1, x2, y2 = bbox
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area > best_area:
                best_area = area
                best_bbox = bbox
        return best_bbox

    def _annotate(
        self,
        frame: np.ndarray,
        camera: CameraEntry,
        signal: dict[str, Any],
        people: list[tuple[int, int, int, int]],
    ) -> np.ndarray:
        if cv2 is None:
            return frame

        for x1, y1, x2, y2 in people:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 255), 2)

        for zone in camera.pos_zones:
            for field_name, (label, color, is_line) in ZONE_ANNOTATION_META.items():
                polygon = getattr(zone, field_name)
                if not polygon:
                    continue
                polygon_points = np.array(polygon, dtype=np.int32)
                cv2.polylines(frame, [polygon_points], not is_line, color, 2)
                cv2.putText(
                    frame,
                    f"{zone.zone_id} {label}",
                    tuple(polygon[0]),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    color,
                    2,
                )

        status_lines = [
            f"{camera.camera_id} | {camera.display_pos_label or camera.pos_terminal_no}",
            f"customers={signal['non_seller_count']} seller-zones={len(signal['zones'])}",
        ]
        for idx, zone_signal in enumerate(signal["zones"], start=1):
            status_lines.append(
                f"{zone_signal['pos_zone']}: seller={zone_signal['seller']} bill_motion={zone_signal['bill_motion']} bill_bg={zone_signal['bill_bg']}"
            )

        y = 28
        for line in status_lines:
            cv2.putText(frame, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
            y += 24
        return frame

    def _encode_frame(self, frame: np.ndarray) -> bytes:
        if cv2 is None:
            return b""
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return encoded.tobytes() if ok else b""

    def _json(self, payload: dict) -> str:
        import json

        return json.dumps(payload)


runtime = CVRuntime(Config(config_dir=str(CONFIG_DIR)), settings.redis_url, DATA_DIR / "buffer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime.start()
    try:
        yield
    finally:
        runtime.stop()


app = FastAPI(title="RLCC CV Debug", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "detector": runtime.detector_name,
        "redis_url": settings.redis_url,
        "buffer_minutes": settings.video_buffer_minutes,
        "camera_count": len(runtime.states),
        "cameras": runtime.cameras(),
    }


@app.get("/cameras")
async def cameras():
    return runtime.cameras()


@app.post("/config/reload")
async def reload_config():
    runtime.reload()
    return {"ok": True, "camera_count": len(runtime.states)}


def _stream_generator(camera_id: str):
    boundary = b"--frame\r\n"
    while True:
        state = runtime.get_state(camera_id)
        frame = state.latest_frame
        if frame:
            yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(0.2)


@app.get("/stream")
async def stream(camera_id: str | None = Query(default=None)):
    state = runtime.get_state(camera_id)
    return StreamingResponse(
        _stream_generator(state.camera.camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/stream/view")
async def stream_view(camera_id: str | None = Query(default=None)):
    state = runtime.get_state(camera_id)
    options = "".join(
        f"<option value=\"{camera['camera_id']}\" {'selected' if camera['camera_id'] == state.camera.camera_id else ''}>{camera['camera_id']} - {camera['display_pos_label']}</option>"
        for camera in runtime.cameras()
    )
    html = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8" />
        <title>RLCC CV Debug</title>
        <style>
          body {{ font-family: sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; }}
          .wrap {{ max-width: 1440px; margin: 0 auto; padding: 24px; }}
          .card {{ background: #111827; border: 1px solid #1f2937; border-radius: 16px; padding: 16px; }}
          img {{ width: 100%; border-radius: 12px; background: #020617; }}
          select {{ padding: 10px 12px; border-radius: 10px; background: #0f172a; color: #e2e8f0; border: 1px solid #334155; }}
          a {{ color: #7dd3fc; }}
          pre {{ background: #020617; padding: 12px; border-radius: 12px; overflow: auto; }}
        </style>
      </head>
      <body>
        <div class="wrap">
          <h1>RLCC CV Debug View</h1>
          <div class="card">
            <form method="get" action="/stream/view">
              <label>Camera</label><br />
              <select name="camera_id" onchange="this.form.submit()">{options}</select>
            </form>
            <p><a href="/zones?camera_id={state.camera.camera_id}">View zones JSON</a></p>
            <img src="/stream?camera_id={state.camera.camera_id}" alt="Live CV stream" />
            <h3>Latest signal</h3>
            <pre>{runtime._json(state.latest_signal or {})}</pre>
          </div>
        </div>
      </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/zones")
async def zones(camera_id: str | None = Query(default=None)):
    if camera_id:
        state = runtime.get_state(camera_id)
        camera = state.camera
        return {
            "camera_id": camera.camera_id,
            "store_id": camera.store_id,
            "pos_terminal_no": camera.pos_terminal_no,
            "zones": {
                "pos_zones": [_zone_payload(zone) for zone in camera.pos_zones]
            },
        }
    return {
        "cameras": [
            {
                "camera_id": state.camera.camera_id,
                "zones": {
                    "pos_zones": [_zone_payload(zone) for zone in state.camera.pos_zones]
                },
            }
            for state in runtime.states.values()
        ]
    }


@app.get("/zones/load")
async def zones_load(camera_id: str):
    return await zones(camera_id=camera_id)


@app.get("/zones/frame")
async def zones_frame(camera_id: str | None = Query(default=None)):
    state = runtime.get_state(camera_id)
    if not state.latest_frame:
        raise HTTPException(status_code=404, detail="Frame not ready")
    return StreamingResponse(iter([state.latest_frame]), media_type="image/jpeg")


@app.post("/zones/save")
async def zones_save(payload: dict):
    camera_id = payload.get("camera_id")
    zones = payload.get("zones", {}).get("pos_zones")
    if not camera_id or not isinstance(zones, list):
        return JSONResponse(status_code=400, content={"ok": False, "message": "camera_id and zones.pos_zones are required"})

    runtime.config.reload()
    camera = runtime.config.get_camera_by_id(camera_id)
    if not camera:
        return JSONResponse(status_code=404, content={"ok": False, "message": "camera not found"})

    for idx, entry in enumerate(runtime.config.cameras):
        if entry.camera_id != camera_id:
            continue
        runtime.config.cameras[idx].pos_zones = [
            PosZoneConfig(
                zone_id=zone["zone_id"],
                **{field_name: get_zone_polygon_value(zone, field_name) or [] for field_name in ZONE_POLYGON_FIELDS},
            )
            for zone in zones
        ]
        break

    runtime.config.save_cameras()
    runtime.config.reload()
    state = runtime.states.get(camera_id)
    if state:
        state.camera = runtime.config.get_camera_by_id(camera_id) or state.camera
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    reload_enabled = os.getenv("CV_RELOAD", "").strip().lower() in {"1", "true", "yes", "on"}
    uvicorn.run("cv.main:app", host=settings.cv_host, port=settings.cv_port, reload=reload_enabled)
