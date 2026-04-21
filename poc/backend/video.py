from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


class VideoManager:
    def __init__(self, data_dir: Path, retention_days: int = 2):
        self.buffer_root = data_dir / "buffer"
        self.snippet_root = data_dir / "snippets"
        self.retention_days = retention_days
        self.buffer_root.mkdir(parents=True, exist_ok=True)
        self.snippet_root.mkdir(parents=True, exist_ok=True)

    def buffer_dir(self, camera_id: str) -> Path:
        path = self.buffer_root / camera_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def snippet_path(self, clip_id: str) -> Path:
        return self.snippet_root / f"{clip_id}.mp4"

    def extract_clip(self, camera_id: str, clip_id: str, start_ts: datetime, end_ts: datetime) -> str:
        segments = self._segments_for_window(camera_id, start_ts, end_ts)
        if not segments:
            return ""

        output_path = self.snippet_path(clip_id)
        start_offset = max(0.0, (start_ts - self._segment_start(segments[0])).total_seconds())
        duration = max(1.0, (end_ts - start_ts).total_seconds())

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as concat_file:
            for segment in segments:
                concat_file.write(f"file '{segment.as_posix()}'\n")
            concat_path = concat_file.name

        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_path,
            "-ss",
            str(start_offset),
            "-t",
            str(duration),
            "-c",
            "copy",
            output_path.as_posix(),
        ]

        try:
            subprocess.run(cmd, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"[video] clip extraction failed for {clip_id}: {exc}")
            return ""
        finally:
            Path(concat_path).unlink(missing_ok=True)

        return output_path.as_posix()

    def cleanup_old_snippets(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        deleted = 0
        for snippet in self.snippet_root.glob("*.mp4"):
            modified = datetime.fromtimestamp(snippet.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                snippet.unlink(missing_ok=True)
                deleted += 1
        return deleted

    def _segments_for_window(self, camera_id: str, start_ts: datetime, end_ts: datetime) -> list[Path]:
        return [
            segment
            for segment in sorted(self.buffer_dir(camera_id).glob("segment_*.mp4"))
            if self._segment_overlaps(segment, start_ts, end_ts)
        ]

    def _segment_overlaps(self, segment: Path, start_ts: datetime, end_ts: datetime) -> bool:
        segment_start = self._segment_start(segment)
        segment_end = segment_start + timedelta(seconds=60)
        return segment_end > start_ts and segment_start < end_ts

    def _segment_start(self, segment: Path) -> datetime:
        stamp = segment.stem.replace("segment_", "")
        return datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)
