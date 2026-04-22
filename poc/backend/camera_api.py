from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
import httpx

import backend.deps as deps
from backend.config import (
    ZONE_POLYGON_FIELDS,
    build_seller_window_id,
    get_zone_polygon_value,
    normalize_terminal,
)

router = APIRouter()


def _normalize_point(point: Any) -> list[int] | None:
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        return None
    try:
        return [int(round(float(point[0]))), int(round(float(point[1])))]
    except (TypeError, ValueError):
        return None


def _normalize_polygon(
    raw_polygon: Any,
    *,
    camera_id: str,
    zone_id: str,
    polygon_name: str,
    errors: list[str],
) -> list[list[int]]:
    if raw_polygon in (None, ""):
        return []
    if not isinstance(raw_polygon, list):
        errors.append(f"{polygon_name} must be a list for camera {camera_id or '<new camera>'} zone {zone_id}")
        return []

    points: list[list[int]] = []
    for point_index, point in enumerate(raw_polygon, start=1):
        normalized = _normalize_point(point)
        if normalized is None:
            errors.append(
                f"{polygon_name} point {point_index} must be [x, y] for camera {camera_id or '<new camera>'} zone {zone_id}"
            )
            continue
        points.append(normalized)
    return points


def _normalize_camera_payload(cameras: list[Any]) -> tuple[list[dict], list[str]]:
    normalized_cameras: list[dict] = []
    errors: list[str] = []
    seen_camera_ids: set[str] = set()
    seen_store_terminals: set[str] = set()

    for camera_index, raw_camera in enumerate(cameras, start=1):
        if not isinstance(raw_camera, dict):
            errors.append(f"Invalid camera entry at index {camera_index}")
            continue

        store_id = str(raw_camera.get("store_id") or "").strip()
        pos_terminal_no = str(
            raw_camera.get("pos_terminal_no") or raw_camera.get("pos_terminal") or raw_camera.get("display_pos_label") or ""
        ).strip()
        display_pos_label = str(raw_camera.get("display_pos_label") or pos_terminal_no).strip()
        camera_id = str(raw_camera.get("camera_id") or "").strip()
        rtsp_url = str(raw_camera.get("rtsp_url") or "").strip()
        xprotect_device_id = str(raw_camera.get("xprotect_device_id") or "").strip()
        seller_window_id = (
            build_seller_window_id(store_id, pos_terminal_no)
            if store_id and pos_terminal_no
            else str(raw_camera.get("seller_window_id") or "").strip()
        )
        raw_zones = raw_camera.get("zones", {})
        zone_entries = raw_zones.get("pos_zones", []) if isinstance(raw_zones, dict) else []

        if not store_id:
            errors.append(f"camera mapping entry {camera_index} is missing store_id")
        if not pos_terminal_no:
            errors.append(f"camera mapping entry {camera_index} is missing pos_terminal_no")
        if not camera_id:
            errors.append(f"camera mapping entry {camera_index} is missing camera_id")

        normalized_camera_id = camera_id.upper()
        if normalized_camera_id:
            if normalized_camera_id in seen_camera_ids:
                errors.append(f"duplicate camera_id `{camera_id}`")
            seen_camera_ids.add(normalized_camera_id)

        store_terminal_key = f"{store_id}:{normalize_terminal(pos_terminal_no)}"
        if store_id and pos_terminal_no:
            if store_terminal_key in seen_store_terminals:
                errors.append(f"duplicate camera mapping for {store_id}:{pos_terminal_no}")
            seen_store_terminals.add(store_terminal_key)

        if not isinstance(zone_entries, list) or not zone_entries:
            errors.append(f"camera mapping missing pos_zones for {camera_id or store_id or f'entry {camera_index}'}")
            zone_entries = []

        normalized_zones: list[dict] = []
        seen_zone_ids: set[str] = set()
        for zone_index, raw_zone in enumerate(zone_entries, start=1):
            if not isinstance(raw_zone, dict):
                errors.append(f"Invalid zone entry at index {zone_index} for camera {camera_id or f'entry {camera_index}'}")
                continue

            zone_id = str(raw_zone.get("zone_id") or "").strip()
            if not normalize_terminal(zone_id):
                errors.append(f"zone {zone_index} is missing zone_id for camera {camera_id or f'entry {camera_index}'}")
                continue

            normalized_zone_id = normalize_terminal(zone_id)
            if normalized_zone_id in seen_zone_ids:
                errors.append(f"duplicate zone_id `{zone_id}` for camera {camera_id or f'entry {camera_index}'}")
                continue
            seen_zone_ids.add(normalized_zone_id)

            normalized_zone = {"zone_id": zone_id}
            for polygon_name in ZONE_POLYGON_FIELDS:
                normalized_zone[polygon_name] = _normalize_polygon(
                    get_zone_polygon_value(raw_zone, polygon_name) or [],
                    camera_id=camera_id,
                    zone_id=zone_id,
                    polygon_name=polygon_name,
                    errors=errors,
                )
            normalized_zones.append(normalized_zone)

        normalized_cameras.append(
            {
                "seller_window_id": seller_window_id,
                "store_id": store_id,
                "pos_terminal_no": pos_terminal_no,
                "display_pos_label": display_pos_label,
                "camera_id": camera_id,
                "rtsp_url": rtsp_url,
                "xprotect_device_id": xprotect_device_id,
                "multi_pos": bool(raw_camera.get("multi_pos", False)),
                "enabled": bool(raw_camera.get("enabled", True)),
                "zones": {
                    "pos_zones": normalized_zones,
                },
            }
        )

    return normalized_cameras, errors


@router.get("/api/camera-mapping")
async def get_camera_mapping():
    return {
        "issues": deps.config.validate_mappings(),
        "cameras": [
            {
                "seller_window_id": camera.seller_window_id,
                "store_id": camera.store_id,
                "pos_terminal_no": camera.pos_terminal_no,
                "display_pos_label": camera.display_pos_label,
                "camera_id": camera.camera_id,
                "rtsp_url": camera.rtsp_url,
                "xprotect_device_id": camera.xprotect_device_id,
                "multi_pos": camera.multi_pos,
                "enabled": camera.enabled,
                "zones": {
                    "pos_zones": [
                        {
                            "zone_id": zone.zone_id,
                            **{polygon_name: getattr(zone, polygon_name) for polygon_name in ZONE_POLYGON_FIELDS},
                        }
                        for zone in camera.pos_zones
                    ]
                },
            }
            for camera in deps.config.cameras
        ],
    }


@router.post("/api/camera-mapping")
async def update_camera_mapping(payload: dict):
    cameras = payload.get("cameras")
    if not isinstance(cameras, list):
        return {"ok": False, "message": "Expected `cameras` list"}

    normalized_cameras, errors = _normalize_camera_payload(cameras)
    if errors:
        return {"ok": False, "message": errors[0], "issues": errors}

    path = deps.config.config_dir / "camera_mapping.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(normalized_cameras, handle, indent=2)
    deps.config.reload()

    cv_reloaded = False
    cv_reload_error = ""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"http://127.0.0.1:{deps.settings.cv_port}/config/reload")
        cv_reloaded = response.is_success
        if not response.is_success:
            cv_reload_error = response.text or f"reload failed with {response.status_code}"
    except Exception as exc:
        cv_reload_error = str(exc)

    return {
        "ok": True,
        "issues": deps.config.validate_mappings(),
        "cv_reloaded": cv_reloaded,
        "cv_reload_error": cv_reload_error,
    }
