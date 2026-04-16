import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class PosZoneConfig:
    zone_id: str
    seller_zone: list[list[int]]
    bill_zone: list[list[int]]


@dataclass
class CameraEntry:
    seller_window_id: str
    store_id: str
    pos_terminal: str
    camera_id: str
    rtsp_url: str
    xprotect_device_id: str
    multi_pos: bool
    pos_zones: list[PosZoneConfig]


@dataclass
class StoreEntry:
    cin: str
    name: str
    pos_system: str


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
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            self.stores = [StoreEntry(cin=s["cin"], name=s["name"], pos_system=s["pos_system"]) for s in data]

    def _load_cameras(self):
        path = self.config_dir / "camera_mapping.json"
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            self.cameras = []
            for c in data:
                zones = [
                    PosZoneConfig(
                        zone_id=z["zone_id"],
                        seller_zone=z["seller_zone"],
                        bill_zone=z["bill_zone"],
                    )
                    for z in c.get("zones", {}).get("pos_zones", [])
                ]
                self.cameras.append(CameraEntry(
                    seller_window_id=c["seller_window_id"],
                    store_id=c["store_id"],
                    pos_terminal=c["pos_terminal"],
                    camera_id=c["camera_id"],
                    rtsp_url=c.get("rtsp_url", ""),
                    xprotect_device_id=c.get("xprotect_device_id", ""),
                    multi_pos=c.get("multi_pos", False),
                    pos_zones=zones,
                ))

    def _load_rules(self):
        path = self.config_dir / "rule_config.json"
        if path.exists():
            with open(path) as f:
                self.rules = json.load(f)

    def save_stores(self):
        path = self.config_dir / "stores.json"
        with open(path, "w") as f:
            json.dump([{"cin": s.cin, "name": s.name, "pos_system": s.pos_system} for s in self.stores], f, indent=2)

    def save_cameras(self):
        path = self.config_dir / "camera_mapping.json"
        data = []
        for c in self.cameras:
            data.append({
                "seller_window_id": c.seller_window_id,
                "store_id": c.store_id,
                "pos_terminal": c.pos_terminal,
                "camera_id": c.camera_id,
                "rtsp_url": c.rtsp_url,
                "xprotect_device_id": c.xprotect_device_id,
                "multi_pos": c.multi_pos,
                "zones": {
                    "pos_zones": [
                        {"zone_id": z.zone_id, "seller_zone": z.seller_zone, "bill_zone": z.bill_zone}
                        for z in c.pos_zones
                    ]
                }
            })
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def save_rules(self):
        path = self.config_dir / "rule_config.json"
        with open(path, "w") as f:
            json.dump(self.rules, f, indent=2)

    def get_camera_by_seller_window(self, seller_window_id: str) -> CameraEntry | None:
        for c in self.cameras:
            if c.seller_window_id == seller_window_id:
                return c
        return None

    def get_camera_by_id(self, camera_id: str) -> CameraEntry | None:
        for c in self.cameras:
            if c.camera_id == camera_id:
                return c
        return None

    def get_store_name(self, cin: str) -> str:
        for s in self.stores:
            if s.cin == cin:
                return s.name
        return cin

    def has_changed(self) -> bool:
        changed = False
        for name in ("stores.json", "camera_mapping.json", "rule_config.json"):
            path = self.config_dir / name
            if path.exists():
                mtime = path.stat().st_mtime
                if self._last_modified.get(name) != mtime:
                    self._last_modified[name] = mtime
                    changed = True
        return changed
