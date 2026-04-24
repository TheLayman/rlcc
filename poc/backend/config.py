from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def normalize_terminal(value: str) -> str:
    """Normalize POS terminal values such as `POS 3` and `POS3` to the same key."""
    return "".join((value or "").upper().split())


def build_seller_window_id(store_id: str, pos_terminal_no: str) -> str:
    return f"{store_id}_{normalize_terminal(pos_terminal_no)}"


ZONE_POLYGON_FIELDS = (
    "seller_zone",
    "customer_zone",
    "midline",
    "pos_zone",
    "pos_screen_zone",
    "bill_zone",
)

ZONE_POLYGON_ALIASES: dict[str, tuple[str, ...]] = {
    "seller_zone": (),
    "customer_zone": (),
    "midline": ("mid_line",),
    "pos_zone": (),
    "pos_screen_zone": ("pos_screen",),
    "bill_zone": ("bill_gen_zone", "bill_genzone"),
}


def get_zone_polygon_value(raw_zone: dict, field_name: str):
    if field_name in raw_zone:
        return raw_zone.get(field_name)
    for alias in ZONE_POLYGON_ALIASES.get(field_name, ()):
        if alias in raw_zone:
            return raw_zone.get(alias)
    return None


@dataclass
class PosZoneConfig:
    zone_id: str
    seller_zone: list[list[int]] = field(default_factory=list)
    customer_zone: list[list[int]] = field(default_factory=list)
    midline: list[list[int]] = field(default_factory=list)
    pos_zone: list[list[int]] = field(default_factory=list)
    pos_screen_zone: list[list[int]] = field(default_factory=list)
    bill_zone: list[list[int]] = field(default_factory=list)

    @property
    def normalized_zone_id(self) -> str:
        return normalize_terminal(self.zone_id)


@dataclass
class CameraEntry:
    seller_window_id: str
    store_id: str
    pos_terminal_no: str
    display_pos_label: str
    camera_id: str
    rtsp_url: str
    xprotect_device_id: str
    multi_pos: bool
    pos_zones: list[PosZoneConfig]
    enabled: bool = True

    @property
    def normalized_terminal(self) -> str:
        return normalize_terminal(self.pos_terminal_no)

    @property
    def seller_window_key(self) -> str:
        return self.seller_window_id or build_seller_window_id(self.store_id, self.pos_terminal_no)

    def matches_terminal(self, store_id: str, pos_terminal_no: str) -> bool:
        return self.store_id == store_id and self.normalized_terminal == normalize_terminal(pos_terminal_no)


@dataclass
class StoreEntry:
    cin: str
    name: str
    pos_system: str
    operator: str = ""


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
        self.stores = []
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[config] Error loading {path}: {e}")
            return

        self.stores = [
            StoreEntry(
                cin=s["cin"],
                name=s["name"],
                pos_system=s["pos_system"],
                operator=s.get("operator", ""),
            )
            for s in data
        ]

    def _load_cameras(self):
        path = self.config_dir / "camera_mapping.json"
        self.cameras = []
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[config] Error loading {path}: {e}")
            return

        loaded: list[CameraEntry] = []
        for c in data:
            pos_terminal_no = c.get("pos_terminal_no") or c.get("pos_terminal") or c.get("display_pos_label", "")
            display_pos_label = c.get("display_pos_label") or pos_terminal_no
            seller_window_id = c.get("seller_window_id") or build_seller_window_id(c["store_id"], pos_terminal_no)

            zones = [
                PosZoneConfig(
                    zone_id=z["zone_id"],
                    seller_zone=get_zone_polygon_value(z, "seller_zone") or [],
                    customer_zone=get_zone_polygon_value(z, "customer_zone") or [],
                    midline=get_zone_polygon_value(z, "midline") or [],
                    pos_zone=get_zone_polygon_value(z, "pos_zone") or [],
                    pos_screen_zone=get_zone_polygon_value(z, "pos_screen_zone") or [],
                    bill_zone=get_zone_polygon_value(z, "bill_zone") or [],
                )
                for z in c.get("zones", {}).get("pos_zones", [])
            ]

            loaded.append(
                CameraEntry(
                    seller_window_id=seller_window_id,
                    store_id=c["store_id"],
                    pos_terminal_no=pos_terminal_no,
                    display_pos_label=display_pos_label,
                    camera_id=c["camera_id"],
                    rtsp_url=c.get("rtsp_url", ""),
                    xprotect_device_id=c.get("xprotect_device_id", ""),
                    multi_pos=bool(c.get("multi_pos", False)),
                    pos_zones=zones,
                    enabled=bool(c.get("enabled", True)),
                )
            )

        self.cameras = loaded

    def _load_rules(self):
        path = self.config_dir / "rule_config.json"
        self.rules = {}
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                self.rules = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[config] Error loading {path}: {e}")

    def save_stores(self):
        path = self.config_dir / "stores.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "cin": s.cin,
                        "name": s.name,
                        "pos_system": s.pos_system,
                        "operator": s.operator,
                    }
                    for s in self.stores
                ],
                f,
                indent=2,
            )

    def save_cameras(self):
        path = self.config_dir / "camera_mapping.json"
        data = []
        for c in self.cameras:
            data.append(
                {
                    "seller_window_id": c.seller_window_id,
                    "store_id": c.store_id,
                    "pos_terminal_no": c.pos_terminal_no,
                    "display_pos_label": c.display_pos_label,
                    "camera_id": c.camera_id,
                    "rtsp_url": c.rtsp_url,
                    "xprotect_device_id": c.xprotect_device_id,
                    "multi_pos": c.multi_pos,
                    "enabled": c.enabled,
                    "zones": {
                        "pos_zones": [
                            {
                                "zone_id": z.zone_id,
                                "seller_zone": z.seller_zone,
                                "customer_zone": z.customer_zone,
                                "midline": z.midline,
                                "pos_zone": z.pos_zone,
                                "pos_screen_zone": z.pos_screen_zone,
                                "bill_zone": z.bill_zone,
                            }
                            for z in c.pos_zones
                        ]
                    },
                }
            )
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def save_rules(self):
        path = self.config_dir / "rule_config.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.rules, f, indent=2)

    def get_store(self, cin: str) -> StoreEntry | None:
        for s in self.stores:
            if s.cin == cin:
                return s
        return None

    def get_store_name(self, cin: str) -> str:
        store = self.get_store(cin)
        return store.name if store else cin

    def get_camera_by_terminal(self, store_id: str, pos_terminal_no: str) -> CameraEntry | None:
        for c in self.cameras:
            if c.enabled and c.matches_terminal(store_id, pos_terminal_no):
                return c
        return None

    def get_camera_by_seller_window(self, seller_window_id: str) -> CameraEntry | None:
        normalized = seller_window_id.upper()
        for c in self.cameras:
            if c.enabled and c.seller_window_key.upper() == normalized:
                return c
        return None

    def get_camera_by_id(self, camera_id: str) -> CameraEntry | None:
        for c in self.cameras:
            if c.enabled and c.camera_id == camera_id:
                return c
        return None

    def get_zone_entry(self, camera_id: str, zone_id: str) -> tuple[CameraEntry, PosZoneConfig] | None:
        normalized_zone = normalize_terminal(zone_id)
        for camera in self.cameras:
            if not camera.enabled or camera.camera_id != camera_id:
                continue
            for zone in camera.pos_zones:
                if zone.normalized_zone_id == normalized_zone:
                    return camera, zone
        return None

    def validate_mappings(self) -> list[str]:
        issues: list[str] = []
        seen_keys: set[str] = set()
        seen_camera_ids: set[str] = set()

        for c in self.cameras:
            if not c.store_id:
                issues.append(f"camera mapping missing store_id for camera {c.camera_id}")
            if not c.pos_terminal_no:
                issues.append(f"camera mapping missing pos_terminal_no for camera {c.camera_id}")
            if not c.camera_id:
                issues.append(f"camera mapping missing camera_id for store {c.store_id}")
            if not c.pos_zones:
                issues.append(f"camera mapping missing pos_zones for {c.store_id}:{c.pos_terminal_no}")

            key = f"{c.store_id}:{c.normalized_terminal}"
            if key in seen_keys:
                issues.append(f"duplicate camera mapping for {c.store_id}:{c.pos_terminal_no}")
            seen_keys.add(key)

            normalized_camera_id = c.camera_id.strip().upper()
            if normalized_camera_id:
                if normalized_camera_id in seen_camera_ids:
                    issues.append(f"duplicate camera_id `{c.camera_id}`")
                seen_camera_ids.add(normalized_camera_id)

            seen_zone_ids: set[str] = set()
            for zone in c.pos_zones:
                normalized_zone_id = zone.normalized_zone_id
                if not normalized_zone_id:
                    issues.append(f"camera mapping has blank zone_id for camera {c.camera_id}")
                    continue
                if normalized_zone_id in seen_zone_ids:
                    issues.append(f"duplicate zone_id `{zone.zone_id}` for camera {c.camera_id}")
                    continue
                seen_zone_ids.add(normalized_zone_id)
                if c.enabled and not zone.seller_zone:
                    issues.append(f"camera {c.camera_id} zone {zone.zone_id} has empty seller_zone — CV seller detection disabled")
                if c.enabled and not zone.bill_zone:
                    issues.append(f"camera {c.camera_id} zone {zone.zone_id} has empty bill_zone — rule 29 (bill not generated) disabled until zones are drawn")

            if not self.get_store(c.store_id):
                issues.append(f"camera mapping references unknown store {c.store_id}")

        return issues

    def has_changed(self) -> bool:
        changed = False
        for name in ("stores.json", "camera_mapping.json", "rule_config.json"):
            path = self.config_dir / name
            if not path.exists():
                continue
            mtime = path.stat().st_mtime
            if self._last_modified.get(name) != mtime:
                self._last_modified[name] = mtime
                changed = True
        return changed
