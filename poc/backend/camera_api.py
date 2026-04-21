from __future__ import annotations

import json

from fastapi import APIRouter
import httpx

import backend.deps as deps

router = APIRouter()


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
                            "seller_zone": zone.seller_zone,
                            "bill_zone": zone.bill_zone,
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
    path = deps.config.config_dir / "camera_mapping.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cameras, handle, indent=2)
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
