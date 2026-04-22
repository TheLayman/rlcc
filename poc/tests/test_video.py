from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import backend.video as video_module
from backend.video import VideoManager


def test_extract_clip_waits_for_future_window(tmp_path):
    manager = VideoManager(tmp_path)
    now = datetime.now(timezone.utc)

    clip = manager.extract_clip(
        camera_id="cam-01",
        clip_id="future-window",
        start_ts=now - timedelta(seconds=10),
        end_ts=now + timedelta(seconds=5),
    )

    assert clip == ""


def test_extract_clip_reencodes_playable_output(monkeypatch, tmp_path):
    manager = VideoManager(tmp_path)
    segment = manager.buffer_dir("cam-01") / "segment_2026-04-22T10-00-00.ts"
    segment.write_bytes(b"transport-stream")

    commands: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        commands.append(cmd)
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"x" * 4096)
            return SimpleNamespace(stdout="")
        if cmd[0] == "ffprobe":
            return SimpleNamespace(stdout="2.75\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(video_module.subprocess, "run", fake_run)

    clip = manager.extract_clip(
        camera_id="cam-01",
        clip_id="playable",
        start_ts=datetime(2026, 4, 22, 10, 0, 10, tzinfo=timezone.utc),
        end_ts=datetime(2026, 4, 22, 10, 0, 25, tzinfo=timezone.utc),
    )

    assert clip == manager.snippet_path("playable").as_posix()
    assert manager.snippet_path("playable").exists()
    assert any(cmd[0] == "ffmpeg" and "libx264" in cmd for cmd in commands)
    assert any(cmd[0] == "ffprobe" for cmd in commands)


def test_extract_clip_discards_zero_duration_output(monkeypatch, tmp_path):
    manager = VideoManager(tmp_path)
    segment = manager.buffer_dir("cam-01") / "segment_2026-04-22T10-00-00.ts"
    segment.write_bytes(b"transport-stream")

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"x" * 4096)
            return SimpleNamespace(stdout="")
        if cmd[0] == "ffprobe":
            return SimpleNamespace(stdout="0.0\n")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(video_module.subprocess, "run", fake_run)

    clip = manager.extract_clip(
        camera_id="cam-01",
        clip_id="zero-duration",
        start_ts=datetime(2026, 4, 22, 10, 0, 10, tzinfo=timezone.utc),
        end_ts=datetime(2026, 4, 22, 10, 0, 25, tzinfo=timezone.utc),
    )

    assert clip == ""
    assert not manager.snippet_path("zero-duration").exists()


def test_extract_clip_waits_for_open_mp4_segment(monkeypatch, tmp_path):
    manager = VideoManager(tmp_path)
    now = datetime.now(timezone.utc)
    stamp = (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H-%M-%S")
    segment = manager.buffer_dir("cam-01") / f"segment_{stamp}.mp4"
    segment.write_bytes(b"x" * 4096)

    def fail_run(*args, **kwargs):
        raise AssertionError("ffmpeg should not run for an unstable MP4 segment")

    monkeypatch.setattr(video_module.subprocess, "run", fail_run)

    clip = manager.extract_clip(
        camera_id="cam-01",
        clip_id="unstable-mp4",
        start_ts=now - timedelta(seconds=20),
        end_ts=now - timedelta(seconds=10),
    )

    assert clip == ""
