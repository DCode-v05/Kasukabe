"""The recording lands where --out said, even when --out is a relative path.

The election-at-dawn-8 run of 2026-09-23 filmed itself for half an hour, kept
all 823 frames, and wrote no video: `encode()` runs ffmpeg with the frames
directory as its working directory, so the relative `--out` the launcher passed
(`../docs/runs/series/election8/video`) resolved against the frames directory
and ffmpeg exited 254 into a directory that does not exist. The frames survived,
so the video was recoverable by hand - but a run that has to be rescued by hand
is a run that reports nothing when nobody is watching.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.record_ui import encode  # noqa: E402


def test_the_paths_ffmpeg_is_given_are_absolute(tmp_path, monkeypatch) -> None:
    frames = tmp_path / "video" / "frames"
    frames.mkdir(parents=True)
    for i in range(3):
        (frames / f"frame-{i:07d}.jpg").write_bytes(b"not really a jpeg")
    monkeypatch.setattr("scripts.record_ui.find_ffmpeg", lambda: "/usr/bin/true")
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)

    encode(Path("video/frames"), Path("video/run.mp4"), 30)

    out = Path(seen["cmd"][-1])
    listing = Path(seen["cmd"][seen["cmd"].index("-i") + 1])
    assert out.is_absolute(), f"ffmpeg would write {out} relative to {seen['cwd']}"
    assert listing.is_absolute()
    assert out == (tmp_path / "video" / "run.mp4").resolve()
