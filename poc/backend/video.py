from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


class VideoManager:
    _SEGMENT_DURATION_SECONDS = 60
    _MP4_SEGMENT_STABILIZATION_SECONDS = 5
    _MIN_CLIP_SIZE_BYTES = 1024
    _MIN_CLIP_DURATION_SECONDS = 0.1

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
        start_ts = self._normalize_timestamp(start_ts)
        end_ts = self._normalize_timestamp(end_ts)
        now = datetime.now(timezone.utc)
        if end_ts <= start_ts or end_ts > now:
            return ""

        segments = self._segments_for_window(camera_id, start_ts, end_ts)
        if not segments:
            return ""
        if any(not self._segment_is_ready(segment, now) for segment in segments):
            return ""

        output_path = self.snippet_path(clip_id)
        temp_output_path = output_path.with_name(f"{output_path.stem}.tmp.mp4")
        start_offset = max(0.0, (start_ts - self._segment_start(segments[0])).total_seconds())
        duration = max(1.0, (end_ts - start_ts).total_seconds())

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as concat_file:
            for segment in segments:
                concat_file.write(f"file '{segment.as_posix()}'\n")
            concat_path = concat_file.name

        base_cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-fflags",
            "+genpts",
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
            "-an",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
        ]
        stream_copy_cmd = [*base_cmd, "-c", "copy", temp_output_path.as_posix()]
        reencode_cmd = [
            *base_cmd,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            temp_output_path.as_posix(),
        ]

        try:
            try:
                subprocess.run(stream_copy_cmd, check=True)
            except subprocess.CalledProcessError:
                # Stream copy can fail on non-keyframe-aligned boundaries.  Fall
                # back to re-encoding so we still produce a clip, just slower.
                temp_output_path.unlink(missing_ok=True)
                subprocess.run(reencode_cmd, check=True)
            if not self.clip_exists(temp_output_path.as_posix()):
                temp_output_path.unlink(missing_ok=True)
                return ""
            temp_output_path.replace(output_path)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"[video] clip extraction failed for {clip_id}: {exc}")
            return ""
        finally:
            Path(concat_path).unlink(missing_ok=True)
            temp_output_path.unlink(missing_ok=True)

        return output_path.as_posix()

    def clip_exists(self, path_value: str) -> bool:
        path = Path(path_value)
        if not path.exists() or not path.is_file():
            return False
        if path.stat().st_size < self._MIN_CLIP_SIZE_BYTES:
            return False
        duration = self._probe_duration_seconds(path)
        return duration is None or duration >= self._MIN_CLIP_DURATION_SECONDS

    def cleanup_old_snippets(self) -> int:
        """Delete snippets older than retention_days.  Also reaps stale .tmp.mp4
        files left over from failed extract_clip runs."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        deleted = 0
        for pattern in ("*.mp4", "*.tmp.mp4"):
            for snippet in self.snippet_root.glob(pattern):
                try:
                    modified = datetime.fromtimestamp(snippet.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    continue
                if modified < cutoff:
                    snippet.unlink(missing_ok=True)
                    deleted += 1
        return deleted

    def disk_free_pct(self) -> float:
        """Free disk percentage on the snippets filesystem."""
        try:
            usage = shutil.disk_usage(self.snippet_root)
        except OSError:
            return 100.0
        if usage.total <= 0:
            return 100.0
        return 100.0 * usage.free / usage.total

    def emergency_purge(self, min_free_pct: float) -> int:
        """Delete oldest snippets one-by-one until free disk >= min_free_pct.
        Returns the number deleted.  Never touches buffer files (those are
        managed by the CV recorder and pruned separately)."""
        if self.disk_free_pct() >= min_free_pct:
            return 0
        candidates = sorted(
            list(self.snippet_root.glob("*.mp4")) + list(self.snippet_root.glob("*.tmp.mp4")),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
        )
        deleted = 0
        for snippet in candidates:
            if self.disk_free_pct() >= min_free_pct:
                break
            snippet.unlink(missing_ok=True)
            deleted += 1
        return deleted

    def _segments_for_window(self, camera_id: str, start_ts: datetime, end_ts: datetime) -> list[Path]:
        return [
            segment
            for segment in self._segment_paths(camera_id)
            if self._segment_overlaps(segment, start_ts, end_ts)
        ]

    def _segment_overlaps(self, segment: Path, start_ts: datetime, end_ts: datetime) -> bool:
        segment_start = self._segment_start(segment)
        segment_end = segment_start + timedelta(seconds=self._SEGMENT_DURATION_SECONDS)
        return segment_end > start_ts and segment_start < end_ts

    def _segment_start(self, segment: Path) -> datetime:
        stamp = segment.stem.replace("segment_", "")
        try:
            return datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=timezone.utc)
        except ValueError:
            modified = datetime.fromtimestamp(segment.stat().st_mtime, tz=timezone.utc)
            return modified - timedelta(seconds=self._SEGMENT_DURATION_SECONDS)

    def _normalize_timestamp(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _segment_paths(self, camera_id: str) -> list[Path]:
        by_stem: dict[str, Path] = {}
        for pattern in ("segment_*.ts", "segment_*.mp4"):
            for segment in self.buffer_dir(camera_id).glob(pattern):
                existing = by_stem.get(segment.stem)
                if existing is None or self._segment_priority(segment) < self._segment_priority(existing):
                    by_stem[segment.stem] = segment
        return [by_stem[stem] for stem in sorted(by_stem)]

    def _segment_priority(self, segment: Path) -> int:
        suffix = segment.suffix.lower()
        if suffix == ".ts":
            return 0
        if suffix == ".mp4":
            return 1
        return 99

    def _segment_is_ready(self, segment: Path, now: datetime) -> bool:
        if segment.suffix.lower() == ".ts":
            return True
        modified = datetime.fromtimestamp(segment.stat().st_mtime, tz=timezone.utc)
        return (now - modified).total_seconds() >= self._MP4_SEGMENT_STABILIZATION_SECONDS

    def _probe_duration_seconds(self, path: Path) -> float | None:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path.as_posix(),
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            return None
        except subprocess.CalledProcessError:
            return 0.0

        try:
            return float((result.stdout or "").strip() or "0")
        except ValueError:
            return 0.0
