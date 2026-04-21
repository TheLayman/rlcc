from __future__ import annotations

import os
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import redis
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from backend.config import CameraEntry, Config, PosZoneConfig
from backend.settings import get_settings

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


class CVRuntime:
    def __init__(self, config: Config, redis_url: str, buffer_root: Path):
        self.config = config
        self.redis = redis.from_url(redis_url)
        self.buffer_root = buffer_root
        self.buffer_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.states: dict[str, CameraState] = {
            camera.camera_id: CameraState(camera=camera) for camera in self.config.cameras if camera.enabled
        }
        self.threads: list[threading.Thread] = []
        self.detector = self._load_detector()
        self.detector_name = self.detector.__class__.__name__ if self.detector is not None else "disabled"

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

    def cameras(self) -> list[dict]:
        with self.lock:
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
        frame_index = 0
        previous_bill_crops: dict[str, np.ndarray | None] = {zone.zone_id: None for zone in camera.pos_zones}
        last_people: list[tuple[int, int, int, int]] = []

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
            frame = None
            if capture is not None and capture.isOpened():
                ok, raw = capture.read()
                if ok and raw is not None:
                    frame = raw
                else:
                    state.last_error = "RTSP frame read failed"

            if frame is None:
                frame = self._placeholder_frame(camera, state.last_error or "Waiting for live stream")
                state.source_mode = "placeholder"

            if frame_index % 3 == 0:
                last_people = self._detect_people(frame)

            signal = self._build_signal(camera, frame, last_people, previous_bill_crops)
            annotated = self._annotate(frame.copy(), camera, signal, last_people)
            encoded = self._encode_frame(annotated)

            with self.lock:
                state.latest_frame = encoded
                state.latest_signal = signal
                state.last_frame_at = iso_now()
                state.running = True
                state.frame_count += 1

            try:
                self.redis.publish(f"cv:{camera.store_id}:{camera.camera_id}", self._json(signal))
            except Exception:
                with self.lock:
                    state.last_error = "Failed to publish CV signal to Redis"

            self._prune_buffer(camera.camera_id)
            frame_index += 1
            time.sleep(0.5)

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
            "-segment_time",
            "60",
            "-strftime",
            "1",
            (out_dir / "segment_%Y-%m-%dT%H-%M-%S.mp4").as_posix(),
        ]
        try:
            return subprocess.Popen(cmd)
        except Exception:
            return None

    def _prune_buffer(self, camera_id: str):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.video_buffer_minutes)
        camera_dir = self.buffer_root / camera_id
        for segment in camera_dir.glob("segment_*.mp4"):
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

    def _build_signal(
        self,
        camera: CameraEntry,
        frame: np.ndarray,
        people: list[tuple[int, int, int, int]],
        previous_bill_crops: dict[str, np.ndarray | None],
    ) -> dict:
        zones = []
        customer_count = 0

        seller_polygons = [np.array(zone.seller_zone, dtype=np.int32) for zone in camera.pos_zones if zone.seller_zone]
        person_centers = [((x1 + x2) // 2, (y1 + y2) // 2) for x1, y1, x2, y2 in people]

        def inside_any_seller(point: tuple[int, int]) -> bool:
            if cv2 is None:
                return False
            for polygon in seller_polygons:
                if cv2.pointPolygonTest(polygon, point, False) >= 0:
                    return True
            return False

        for point in person_centers:
            if not inside_any_seller(point):
                customer_count += 1

        for zone in camera.pos_zones:
            seller_present = False
            for point in person_centers:
                if zone.seller_zone and cv2 is not None and cv2.pointPolygonTest(
                    np.array(zone.seller_zone, dtype=np.int32), point, False
                ) >= 0:
                    seller_present = True
                    break
            bill_motion, bill_bg = self._bill_zone_status(frame, zone, previous_bill_crops)
            zones.append(
                {
                    "pos_zone": zone.zone_id,
                    "seller": seller_present,
                    "bill_motion": bill_motion,
                    "bill_bg": bill_bg,
                }
            )

        return {
            "ts": iso_now(),
            "store_id": camera.store_id,
            "camera_id": camera.camera_id,
            "zones": zones,
            "non_seller_count": customer_count,
            "non_seller_present": customer_count > 0,
        }

    def _bill_zone_status(
        self,
        frame: np.ndarray,
        zone: PosZoneConfig,
        previous_bill_crops: dict[str, np.ndarray | None],
    ) -> tuple[bool, bool]:
        if cv2 is None or not zone.bill_zone:
            return False, False
        x1, y1, x2, y2 = _polygon_bbox(zone.bill_zone)
        crop = frame[max(y1, 0):max(y2, 0), max(x1, 0):max(x2, 0)]
        if crop.size == 0:
            return False, False
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        previous = previous_bill_crops.get(zone.zone_id)
        previous_bill_crops[zone.zone_id] = gray
        if previous is None or previous.shape != gray.shape:
            return False, False
        diff = cv2.absdiff(gray, previous)
        mean_diff = float(np.mean(diff))
        return mean_diff > 8.0, mean_diff > 18.0

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
            if zone.seller_zone:
                seller_poly = np.array(zone.seller_zone, dtype=np.int32)
                cv2.polylines(frame, [seller_poly], True, (0, 255, 0), 2)
                cv2.putText(frame, f"{zone.zone_id} SELLER", tuple(zone.seller_zone[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            if zone.bill_zone:
                bill_poly = np.array(zone.bill_zone, dtype=np.int32)
                cv2.polylines(frame, [bill_poly], True, (255, 200, 0), 2)
                cv2.putText(frame, f"{zone.zone_id} BILL", tuple(zone.bill_zone[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 200, 0), 2)

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
    return {
        "cameras": [
            {
                "camera_id": state.camera.camera_id,
                "zones": {
                    "pos_zones": [
                        {
                            "zone_id": zone.zone_id,
                            "seller_zone": zone.seller_zone,
                            "bill_zone": zone.bill_zone,
                        }
                        for zone in state.camera.pos_zones
                    ]
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
                seller_zone=zone.get("seller_zone", []),
                bill_zone=zone.get("bill_zone", []),
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

    uvicorn.run("cv.main:app", host=settings.cv_host, port=settings.cv_port, reload=True)
