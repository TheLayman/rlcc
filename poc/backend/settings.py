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
    external_sales_url: str
    external_sales_header_token: str
    torch_whl_index_url: str
    sales_reconciliation_minutes: int
    sales_reconciliation_lookback_minutes: int


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
        external_sales_url=os.getenv("EXTERNAL_SALES_URL", ""),
        external_sales_header_token=os.getenv("EXTERNAL_SALES_HEADER_TOKEN", ""),
        torch_whl_index_url=os.getenv("TORCH_WHL_INDEX_URL", "https://download.pytorch.org/whl/cu124"),
        sales_reconciliation_minutes=int(os.getenv("SALES_RECONCILIATION_MINUTES", "2")),
        sales_reconciliation_lookback_minutes=int(os.getenv("SALES_RECONCILIATION_LOOKBACK_MINUTES", "10")),
    )
