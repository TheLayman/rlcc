from __future__ import annotations

import os
from typing import Any

import numpy as np

from backend.config import PosZoneConfig

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover
    YOLO = None

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None


ACTIVITY_IDLE = "idle"
ACTIVITY_HANDLING_ITEM = "handling_item"
ACTIVITY_HANDLING_CASH = "handling_cash"
ACTIVITY_USING_POS = "using_pos"
ACTIVITY_GIVING_RECEIPT = "giving_receipt"


COCO_LEFT_WRIST = 9
COCO_RIGHT_WRIST = 10
COCO_NOSE = 0


def _inflate_polygon(polygon: list[list[int]], padding: int) -> np.ndarray | None:
    if not polygon:
        return None
    arr = np.asarray(polygon, dtype=np.int32)
    center = arr.mean(axis=0)
    direction = arr - center
    norms = np.linalg.norm(direction, axis=1, keepdims=True)
    norms[norms == 0] = 1
    inflated = arr + (direction / norms) * padding
    return inflated.astype(np.int32)


def _point_in_polygon(polygon: np.ndarray | None, point: tuple[float, float]) -> bool:
    if polygon is None or cv2 is None or len(polygon) < 3:
        return False
    return cv2.pointPolygonTest(polygon, point, False) >= 0


class SellerActivityClassifier:
    """Heuristic seller-activity classifier using YOLOv8 pose keypoints.

    Classifies the seller's dominant-hand position relative to the configured
    per-POS zones (bill_zone, pos_screen_zone, pos_zone) to infer activity:
        - giving_receipt: hand near the receipt printer (bill_zone)
        - using_pos:      hand near the POS screen (pos_screen_zone)
        - handling_cash:  hand near the cash drawer (pos_zone)
        - handling_item:  hand near items (customer side of the counter)
        - idle:           none of the above
    """

    def __init__(self, model_path: str | None = None, padding: int = 30):
        self._model = None
        self._device: Any = "cpu"
        self._padding = padding
        if YOLO is None or cv2 is None:
            return

        force_cpu = os.getenv("CV_FORCE_CPU", "0").strip().lower() in {"1", "true", "yes", "on"}
        use_gpu = torch is not None and torch.cuda.is_available() and not force_cpu
        self._device = 0 if use_gpu else "cpu"

        path = (model_path or os.getenv("POSE_MODEL_PATH", "yolov8n-pose.pt")).strip()
        try:
            self._model = YOLO(path)
        except Exception as exc:  # pragma: no cover - runtime dependency
            print(f"[activity] failed to load pose model {path}: {exc}")
            self._model = None

    @property
    def enabled(self) -> bool:
        return self._model is not None

    def _extract_active_hand(
        self,
        frame: np.ndarray,
        seller_bbox: tuple[int, int, int, int],
    ) -> tuple[float, float, float] | None:
        """Run pose inference on the seller crop and return the dominant
        wrist keypoint translated to frame coordinates.  Returns None if
        the model is disabled, the crop is empty, no pose is detected, or
        both wrists are below the confidence threshold.

        Shared by classify() and extract_hand_zone_flags() so we never run
        pose inference twice for the same frame."""
        if self._model is None or cv2 is None:
            return None

        height, width = frame.shape[:2]
        x1, y1, x2, y2 = seller_bbox
        x1 = max(0, min(x1, width - 1))
        y1 = max(0, min(y1, height - 1))
        x2 = max(x1 + 1, min(x2, width))
        y2 = max(y1 + 1, min(y2, height))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None

        try:
            results = self._model.predict(
                crop,
                imgsz=320,
                conf=0.25,
                verbose=False,
                device=self._device,
            )
        except Exception:
            return None

        if not results or results[0].keypoints is None:
            return None

        keypoints_xy = results[0].keypoints.xy
        if keypoints_xy is None or len(keypoints_xy) == 0:
            return None

        kpts = keypoints_xy[0].cpu().numpy().astype(float)
        conf_tensor = results[0].keypoints.conf
        confidences = (
            conf_tensor[0].cpu().numpy().astype(float)
            if conf_tensor is not None
            else np.ones(len(kpts), dtype=float)
        )

        # Translate crop-space coords back to frame-space.
        kpts[:, 0] += x1
        kpts[:, 1] += y1

        left_wrist = self._keypoint(kpts, confidences, COCO_LEFT_WRIST)
        right_wrist = self._keypoint(kpts, confidences, COCO_RIGHT_WRIST)

        candidates = [w for w in (right_wrist, left_wrist) if w is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda w: w[2])

    def extract_hand_zone_flags(
        self,
        frame: np.ndarray,
        seller_bbox: tuple[int, int, int, int],
        zone: PosZoneConfig,
    ) -> tuple[bool, bool]:
        """Return (bill_hand_present, screen_hand_present) for the seller's
        dominant wrist.  Cheap per-frame signal — runs one pose inference
        and two polygon tests.  No classification."""
        hand = self._extract_active_hand(frame, seller_bbox)
        if hand is None:
            return False, False
        hand_point = (hand[0], hand[1])
        bill_poly = _inflate_polygon(zone.bill_zone, self._padding) if zone.bill_zone else None
        screen_poly = (
            _inflate_polygon(zone.pos_screen_zone, self._padding) if zone.pos_screen_zone else None
        )
        return (
            _point_in_polygon(bill_poly, hand_point),
            _point_in_polygon(screen_poly, hand_point),
        )

    def classify(
        self,
        frame: np.ndarray,
        seller_bbox: tuple[int, int, int, int],
        zone: PosZoneConfig,
    ) -> tuple[str, float]:
        """Return (activity_label, confidence 0..1) from a seller crop.

        seller_bbox is in full-frame coordinates.  Zone polygons are also
        in full-frame coordinates, so we translate the pose keypoints back
        to frame-space before the zone tests.
        """
        hand = self._extract_active_hand(frame, seller_bbox)
        if hand is None:
            return ACTIVITY_IDLE, 0.0

        hand_point = (hand[0], hand[1])
        hand_conf = min(1.0, max(0.0, hand[2]))

        bill_poly = _inflate_polygon(zone.bill_zone, self._padding) if zone.bill_zone else None
        screen_poly = (
            _inflate_polygon(zone.pos_screen_zone, self._padding) if zone.pos_screen_zone else None
        )
        cash_poly = _inflate_polygon(zone.pos_zone, self._padding) if zone.pos_zone else None
        customer_poly = (
            _inflate_polygon(zone.customer_zone, self._padding) if zone.customer_zone else None
        )
        seller_poly = (
            _inflate_polygon(zone.seller_zone, self._padding) if zone.seller_zone else None
        )

        if _point_in_polygon(bill_poly, hand_point):
            return ACTIVITY_GIVING_RECEIPT, hand_conf
        if _point_in_polygon(screen_poly, hand_point):
            return ACTIVITY_USING_POS, hand_conf
        if _point_in_polygon(cash_poly, hand_point):
            return ACTIVITY_HANDLING_CASH, hand_conf

        # If the hand is on the customer side of the seller zone (below/past
        # the midline) treat it as handling an item for the customer.
        if _point_in_polygon(customer_poly, hand_point):
            return ACTIVITY_HANDLING_ITEM, hand_conf
        if seller_poly is not None and not _point_in_polygon(seller_poly, hand_point):
            return ACTIVITY_HANDLING_ITEM, hand_conf * 0.7

        return ACTIVITY_IDLE, hand_conf

    @staticmethod
    def _keypoint(
        keypoints: np.ndarray,
        confidences: np.ndarray,
        index: int,
    ) -> tuple[float, float, float] | None:
        if index >= len(keypoints) or index >= len(confidences):
            return None
        x, y = keypoints[index]
        conf = float(confidences[index])
        if conf < 0.3 or (x == 0 and y == 0):
            return None
        return float(x), float(y), conf
