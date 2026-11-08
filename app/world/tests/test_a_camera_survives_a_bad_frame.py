"""A camera that loses one frame must not lose the whole video.

Found by rehearsing the six-camera setup before spending box hours on it.
All six recorders filmed happily for minutes and then died, at different
times, with the same error:

    RuntimeError: Page.captureScreenshot: {'code': -32603, 'message': 'Internal error'}

Two defects, and the second is the expensive one:

1. The screenshot call had no handler, so a transient failure escaped
   `record()` and killed the process.
2. `encode()` sat AFTER the try/finally, so a process dying that way never
   built its mp4. 43-62 frames per view were sitting on disk and not one
   run.mp4 existed.

Over a 4-8 hour run with six cameras, a transient failure is not a risk, it
is a near certainty - and under the old code each one cost that view its
entire video.
"""

from __future__ import annotations

import ast
from pathlib import Path

RECORDER = Path(__file__).resolve().parents[2] / "scripts" / "record_ui.py"


def _record_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse(RECORDER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "record":
            return node
    raise AssertionError("record() not found in record_ui.py")


def _calls_named(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) == name or getattr(n.func, "attr", None) == name)
    ]


def test_the_screenshot_is_inside_a_handler() -> None:
    """The defect itself: one unguarded await took the whole recorder down."""
    for node in ast.walk(_record_fn()):
        if not isinstance(node, ast.Try):
            continue
        shots = [
            c for c in _calls_named(node, "call")
            if any(
                isinstance(a, ast.Constant) and a.value == "Page.captureScreenshot"
                for a in c.args
            )
        ]
        if shots and node.handlers:
            return
    raise AssertionError("Page.captureScreenshot is not inside a try with a handler")


def test_the_encode_runs_even_when_the_recorder_is_dying() -> None:
    """`encode()` after the finally is `encode()` that a crash skips. The
    frames survive on disk either way; the video only survives if the encode
    is reached."""
    fn = _record_fn()
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
    assert tries, "record() has no try/finally"

    in_finally = any(_calls_named(ast.Module(body=t.finalbody, type_ignores=[]), "encode") for t in tries)
    assert in_finally, (
        "encode() is not in a finally - a recorder that raises loses its video "
        "even though every frame is on disk"
    )


def test_a_failed_encode_says_where_the_frames_are() -> None:
    """The frames are the recording; the mp4 is a convenience built from them.
    If the encode fails the operator needs to know they have not lost the run,
    and `--encode-only` is the way back."""
    source = RECORDER.read_text(encoding="utf-8")
    assert "--encode-only" in source
    assert "the frames are kept in" in source


def test_a_camera_that_can_never_film_does_not_spin_for_hours() -> None:
    """The other half. Tolerating failures forever would turn a dead Chrome
    into a process that looks alive for the rest of a 4-hour run."""
    source = RECORDER.read_text(encoding="utf-8")
    assert "--blind-limit" in source, "no ceiling on consecutive failures"

    fn = _record_fn()
    breaks = [n for n in ast.walk(fn) if isinstance(n, ast.Break)]
    assert len(breaks) >= 2, (
        "expected a break for the blind limit alongside the watch-misses one"
    )


def test_a_good_frame_clears_the_count() -> None:
    """Consecutive, not cumulative. A camera that drops one frame an hour is
    working; resetting on success is what keeps it running."""
    source = RECORDER.read_text(encoding="utf-8")
    assert "blind = 0" in source, "the failure count is never reset, so it is cumulative"


def test_chrome_is_started_with_the_flags_a_cloud_box_needs() -> None:
    """Without these two, a Linux box records nothing and says why in a
    message that names neither of them.

    Chrome's sandbox needs user namespaces, which a hardened or containerised
    host often withholds. Chrome then starts, opens its DevTools port, and
    dies as it renders - so the recorder connects and the socket closes under
    it: "no close frame received or sent", once per frame, forever. Chrome's
    own explanation goes to DEVNULL.

    This cost a real cloud run its video: six cameras alive, zero frames, and
    a log that blamed the websocket.

    `--disable-dev-shm-usage` is the matching one. /dev/shm is commonly 64 MB
    on these hosts, and six 1920x1080 tabs do not fit; the renderer then dies
    under load rather than at startup, which looks like a different bug.
    """
    source = RECORDER.read_text(encoding="utf-8")
    launch = source[source.index("chrome = subprocess.Popen("):]
    launch = launch[: launch.index(")")]

    assert '"--no-sandbox"' in launch, "Chrome will die on a box without user namespaces"
    assert '"--disable-dev-shm-usage"' in launch, "six 1080p tabs will not fit in a 64 MB /dev/shm"
