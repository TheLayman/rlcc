from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env(env_path: Path | None = None) -> None:
    load_dotenv(env_path or (ROOT_DIR / ".env"), override=False)


@dataclass
class Settings:
    push_auth_key: str
    redis_url: str
    backend_host: str
    backend_port: int
    cv_host: str
    cv_port: int
    dashboard_port: int
    video_buffer_minutes: int
    video_retention_days: int
    torch_whl_index_url: str
    dashboard_origins: list[str]


def _parse_origins(raw: str) -> list[str]:
    """Parse comma-separated DASHBOARD_ORIGINS env var.  Empty → ['*']
    (permissive default keeps existing dev workflows working).  Any list
    of explicit origins disables the wildcard."""
    if not raw:
        return ["*"]
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    return parts or ["*"]


def get_settings(env_path: Path | None = None) -> Settings:
    _load_env(env_path)
    return Settings(
        push_auth_key=os.getenv("NUKKAD_PUSH_AUTH_KEY", "test"),
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379"),
        backend_host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        backend_port=int(os.getenv("BACKEND_PORT", "8001")),
        cv_host=os.getenv("CV_HOST", "0.0.0.0"),
        cv_port=int(os.getenv("CV_PORT", "8000")),
        dashboard_port=int(os.getenv("DASHBOARD_PORT", "5173")),
        video_buffer_minutes=int(os.getenv("VIDEO_BUFFER_MINUTES", "10")),
        video_retention_days=int(os.getenv("VIDEO_RETENTION_DAYS", "7")),
        torch_whl_index_url=os.getenv("TORCH_WHL_INDEX_URL", "https://download.pytorch.org/whl/cu124"),
        dashboard_origins=_parse_origins(os.getenv("DASHBOARD_ORIGINS", "")),
    )
