"""Record the township viewer while a run is going, as a video.

    .venv/bin/python scripts/record_ui.py --out ../docs/runs/series/NAME/video \
        --url "http://127.0.0.1:8400/township-v2.html?book=election"

Starts headless Chrome at a fixed window size, opens the viewer, and takes a
frame every `--interval` seconds through the DevTools protocol for as long as
it runs. On SIGINT/SIGTERM (or `--seconds`), ffmpeg stitches the frames into
an MP4 at `--fps`. Frames are written to disk as they are taken, so a run
that is killed still leaves a video: `--encode-only` rebuilds it from them.

Why frames and not a screen recording: a run is hours long and unattended,
headless Chrome needs no screen-recording permission, and a frame every two
seconds of a multi-hour run is a watchable time-lapse (3 hours -> 5,400
frames -> 3 minutes at 30 fps) instead of a 20 GB file. The page itself stays
open the whole time, so its animations and live polling run as they would
for a person watching - a frame is a photograph of that, not a reload.

Operator tooling, not part of the world: nothing under world/ or game/
imports it (app/CONVENTIONS.md).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    # Windows. `shutil.which("chrome")` does not find these - Chrome is not on
    # PATH there - so the absolute paths have to be listed or find_chrome()
    # falls through to candidate[0] and Popen dies with WinError 2, which
    # names no file and reads like a missing script.
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
)


def find_chrome() -> str:
    """A Chrome or Chromium this machine can run headless.

    The Mac has Google Chrome; the team box (Linux, no sudo) may have only a
    Chromium - or Playwright's, which `uv pip install playwright && python -m
    playwright install chromium` puts under ~/.cache without root. Checked in
    that order; the first that exists wins.
    """
    for candidate in CHROME_CANDIDATES:
        # `is_absolute()` rather than `startswith("/")`, so a Windows
        # `C:/...` candidate is treated as the path it is instead of being
        # handed to `which`, which looks along PATH and will not find it.
        path = candidate if Path(candidate).is_absolute() else shutil.which(candidate)
        if path and Path(path).exists():
            return path
    for found in sorted(Path.home().glob(".cache/ms-playwright/chromium*/chrome-linux*/chrome"), reverse=True):
        return str(found)
    return CHROME_CANDIDATES[0]


def find_ffmpeg() -> "str | None":
    """The system ffmpeg, or the one the imageio-ffmpeg wheel ships (no root needed)."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg  # noqa: PLC0415 - optional
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - not installed
        return None


def encode(frames: Path, out_mp4: Path, fps: int) -> "Path | None":
    """Frames -> MP4 (H.264, yuv420p so any player opens it). None when
    there is nothing to encode or ffmpeg is missing."""
    pictures = sorted(frames.glob("frame-*.jpg"))
    if not pictures:
        print("record_ui: no frames to encode", file=sys.stderr)
        return None
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        print("record_ui: ffmpeg not found (install ffmpeg, or `pip install imageio-ffmpeg`); "
              "frames kept in", frames, file=sys.stderr)
        return None
    listing = frames / "frames.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in pictures), encoding="utf-8")
    # ABSOLUTE, both of them. ffmpeg runs with the frames directory as its
    # working directory (the listing names frames without a path), so a
    # relative --out - which is what a launcher started from app/ passes,
    # `../docs/runs/series/NAME/video` - resolved against the frames
    # directory instead, and ffmpeg exited 254 into a directory that does
    # not exist. The election-at-dawn-8 run of 2026-09-23 kept all 823
    # frames and wrote no video.
    out_mp4 = Path(out_mp4).resolve()
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-r", str(fps), "-f", "concat", "-safe", "0",
           "-i", str(listing.resolve()), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", str(out_mp4)]
    subprocess.run(cmd, check=True, cwd=frames)
    print(f"record_ui: {len(pictures)} frames -> {out_mp4} "
          f"({len(pictures) / fps:.0f}s at {fps} fps)")
    return out_mp4


async def _page_socket(port: int, url: str, timeout: float = 30.0) -> str:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                tabs = (await client.get(f"http://127.0.0.1:{port}/json/list")).json()
                pages = [t for t in tabs if t.get("type") == "page"]
                if pages:
                    return pages[0]["webSocketDebuggerUrl"]
            except Exception:  # noqa: BLE001 - Chrome is still starting
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError("Chrome did not open a debuggable page in time")


class Page:
    """The few DevTools calls this needs, over one websocket."""

    def __init__(self, ws) -> None:
        self.ws, self.next_id = ws, 0

    async def call(self, method: str, **params):
        self.next_id += 1
        mine = self.next_id
        await self.ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == mine:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})


async def record(args) -> int:
    frames = Path(args.out) / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    start = len(list(frames.glob("frame-*.jpg")))   # continue numbering after a restart
    profile = tempfile.mkdtemp(prefix="record-ui-")
    # `--no-sandbox` and `--disable-dev-shm-usage` are for Linux, and without
    # them a cloud box records nothing at all.
    #
    # Chrome's sandbox needs user namespaces, which a hardened or containerised
    # host often does not grant. Chrome then starts, opens its DevTools port
    # and dies the moment it renders, so the recorder connects and the socket
    # closes under it: "no close frame received or sent", once per frame,
    # forever. The message never mentions the sandbox, and Chrome's own
    # complaint goes to DEVNULL below.
    #
    # `--disable-dev-shm-usage` is the matching one: /dev/shm is commonly 64 MB
    # on these hosts and six 1920x1080 tabs do not fit, which produces the same
    # kind of unexplained renderer death under load rather than at startup.
    #
    # Both are harmless on a Mac, which is why they are not conditioned on the
    # platform: one code path that works everywhere beats two that differ in
    # the way that is hardest to test.
    chrome = subprocess.Popen(
        [args.chrome, "--headless=new", f"--remote-debugging-port={args.port}",
         f"--user-data-dir={profile}", f"--window-size={args.width},{args.height}",
         "--hide-scrollbars", "--mute-audio", "--no-first-run", "--disable-gpu",
         "--no-sandbox", "--disable-dev-shm-usage", args.url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # `add_signal_handler` raises NotImplementedError on Windows -
        # asyncio simply does not implement it on the Proactor loop. It took
        # the recorder down on startup, so a run on Windows produced a video
        # directory and no frames while everything else worked. `signal.signal`
        # does exist there; it runs the handler on the main thread rather than
        # inside the loop, which is why `call_soon_threadsafe` is used to set
        # the event from it.
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    taken = 0
    try:
        async with websockets.connect(await _page_socket(args.port, args.url), max_size=64 * 1024 * 1024) as ws:
            page = Page(ws)
            await page.call("Emulation.setDeviceMetricsOverride", width=args.width,
                            height=args.height, deviceScaleFactor=1, mobile=False)
            await page.call("Page.enable")
            await asyncio.sleep(args.settle)
            began = time.monotonic()
            print(f"record_ui: recording {args.url} at {args.width}x{args.height}, "
                  f"a frame every {args.interval}s -> {frames}")
            misses = 0
            blind = 0
            async with httpx.AsyncClient(timeout=5) as probe:
              while not stop.is_set():
                # Stop when the world stops: every run ends with the world
                # going away, and frames of an "unreachable" page after the
                # result would be the last thing the video shows.
                if args.watch_url:
                    try:
                        ok = (await probe.get(args.watch_url)).status_code == 200
                    except Exception:  # noqa: BLE001 - down is the answer
                        ok = False
                    misses = 0 if ok else misses + 1
                    if misses >= args.watch_misses:
                        print(f"record_ui: {args.watch_url} stopped answering; stopping")
                        break
                # A screenshot can fail transiently - Chrome answered
                # `Page.captureScreenshot: {'code': -32603, 'message':
                # 'Internal error'}` on all six cameras of a rehearsal, at
                # different times, after minutes of filming happily. Left
                # unhandled it escaped `record()` and killed the process,
                # which lost the ENCODE as well: the frames sat on disk and
                # no run.mp4 was ever written. One bad frame must cost one
                # frame. Only a camera that cannot take any frame at all is
                # actually broken, and `blind` is what tells those apart.
                try:
                    shot = await page.call(
                        "Page.captureScreenshot", format="jpeg", quality=args.quality
                    )
                except Exception as exc:  # noqa: BLE001 - any failure is one lost frame
                    blind += 1
                    print(f"record_ui: frame {start + taken} failed ({exc}); "
                          f"{blind} in a row, continuing")
                    if blind >= args.blind_limit:
                        print(f"record_ui: {blind} failures in a row; giving up on new "
                              f"frames and encoding the {taken} already taken")
                        break
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=args.interval)
                    except asyncio.TimeoutError:
                        pass
                    continue
                blind = 0
                (frames / f"frame-{start + taken:07d}.jpg").write_bytes(base64.b64decode(shot["data"]))
                taken += 1
                if args.seconds and time.monotonic() - began >= args.seconds:
                    break
                try:
                    await asyncio.wait_for(stop.wait(), timeout=args.interval)
                except asyncio.TimeoutError:
                    pass
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()
        shutil.rmtree(profile, ignore_errors=True)
        # Inside the `finally`, because it used to sit after it: any exception
        # out of the loop skipped the encode entirely and a run that had filmed
        # for hours ended with frames on disk and no video. Whatever was
        # filmed gets encoded, even when the recorder is dying.
        print(f"record_ui: {taken} frames taken this session")
        try:
            encode(frames, Path(args.out) / "run.mp4", args.fps)
        except Exception as exc:  # noqa: BLE001 - the frames are still on disk
            print(f"record_ui: could not encode ({exc}); the frames are kept in "
                  f"{frames} - `--encode-only` builds the video from them later")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8400/township-v2.html?book=election")
    ap.add_argument("--out", required=True, help="directory for frames/ and run.mp4")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between frames")
    ap.add_argument("--fps", type=int, default=30, help="frames per second in the video")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--quality", type=int, default=80, help="JPEG quality per frame")
    ap.add_argument("--settle", type=float, default=6.0, help="seconds to let the page load first")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = until signalled)")
    # 15 consecutive failures is a minute of blindness at the default 4s
    # interval. Below that a camera is having a bad moment; above it, Chrome
    # is gone and there is nothing to wait for.
    ap.add_argument("--blind-limit", type=int, default=15,
                    help="give up after this many consecutive failed screenshots")
    # Outside the engine range (preflight.ENGINE_PORT_BASE 9100, one port per
    # resident, up to 9349 at 250): 9333 sat inside it, and preflight
    # reported the recorder's Chrome as an engine port already taken.
    ap.add_argument("--port", type=int, default=9600, help="Chrome's DevTools port")
    ap.add_argument("--chrome", default=None, help="Chrome/Chromium binary (found automatically)")
    ap.add_argument("--watch-url", default="", help="stop when this URL stops answering (the world's /world/state)")
    ap.add_argument("--watch-misses", type=int, default=3, help="consecutive failures before stopping")
    ap.add_argument("--encode-only", action="store_true", help="only rebuild run.mp4 from saved frames")
    args = ap.parse_args(argv)
    args.chrome = args.chrome or find_chrome()
    if args.encode_only:
        return 0 if encode(Path(args.out) / "frames", Path(args.out) / "run.mp4", args.fps) else 1
    return asyncio.run(record(args))


if __name__ == "__main__":
    sys.exit(main())
