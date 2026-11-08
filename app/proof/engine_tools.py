"""The other registry: the verbs the engine serves the model directly.

Not every verb a resident can call passes through `world/tools/registry.py`.
Three of them - `recall_memories`, `recall_session_log` and `search_memory` -
are built and registered in `engine/src/sim/pinned_boot.py`, onto the same
registry the `world:*` tools reach the model through. A resident calling
`recall_memories` is not calling a withdrawn verb; it is calling a verb this
package was not looking at.

That mattered: `replay` reported six recorded shapes as "the registry no
longer serves this verb" and `reach` reported three verbs as absent, across
59 calls a real model made and a real world answered. Both were the
instrument's blind spot rather than the town's defect, and an instrument that
cries withdrawal is as corrosive as one that ticks green for code that never
ran.

HOW IT IS RESOLVED, AND WHY NOT BY IMPORT. The engine has its own
interpreter and its own dependencies (`aiosqlite`, among others) which the
app's virtualenv does not carry, so `import src.sim.memory_stream` from here
raises ModuleNotFoundError. The answer is not to read the source and parse
the schema out of it - this package decides nothing by reading source. It is
to run the real builders where they live and read back what they return, as
JSON, once per process.

WHEN THE ENGINE CANNOT BE REACHED the answer is None, and every caller keeps
its UNPROVEN verdict and says the engine could not be reached. An instrument
that guesses when its evidence is missing is the thing being guarded against.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ENGINE = APP.parent / "engine"

ENGINE_SIDE_VERBS = ("recall_memories", "recall_session_log", "search_memory")

_PROBE = """
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, %r)
from src.sim.memory_stream import build_memory_stream_tool
from src.sim.session_log import build_session_log_tool
from src.sim.memory_query import build_search_memory_tool

tmp = Path(tempfile.mkdtemp())
tools = [
    build_memory_stream_tool(tmp / "memory_stream.jsonl"),
    build_session_log_tool(tmp / "session_log.jsonl"),
    build_search_memory_tool(None),
]
print(json.dumps({
    t.name: {
        "properties": sorted((t.input_schema or {}).get("properties", {})),
        "required": list((t.input_schema or {}).get("required", [])),
    }
    for t in tools
}))
"""


def _interpreter() -> Path | None:
    for candidate in (ENGINE / ".venv" / "bin" / "python", ENGINE / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return candidate
    return None


@lru_cache(maxsize=1)
def engine_tool_schemas() -> dict[str, dict] | None:
    """`{verb: {"properties": [...], "required": [...]}}`, or None.

    None means the engine's interpreter could not be run here - not that the
    verbs are absent. The difference is the whole point of this package.
    """
    python = _interpreter()
    if python is None:
        return None
    try:
        done = subprocess.run(
            [str(python), "-c", _PROBE % str(ENGINE)],
            capture_output=True, text=True, timeout=120, cwd=str(ENGINE),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    try:
        schemas = json.loads(done.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return None
    return schemas if isinstance(schemas, dict) and schemas else None


def shape_fits(schema: dict, params: Iterable[str]) -> str | None:
    """None when this argument spelling would be accepted, else why not.

    The same two questions the world's validator asks: is every parameter one
    the verb declares, and is every required parameter present. Types are not
    checked here - the corpus records argument NAMES, which is where the
    breakages this package hunts actually happen (a rename, a drop, a
    tightening), and claiming to check more than the evidence carries would
    be the instrument overreaching.
    """
    declared = set(schema.get("properties") or ())
    required = set(schema.get("required") or ())
    given = set(params)
    stray = sorted(given - declared)
    if stray:
        return f"no such parameter {', '.join(repr(s) for s in stray)}; takes: {', '.join(sorted(declared)) or 'no parameters'}"
    missing = sorted(required - given)
    if missing:
        return f"{', '.join(missing)}: required"
    return None


def served_engine_side(verb: str) -> tuple[bool, str] | None:
    """Is this verb registered by the engine? `(served, sentence)`, or None.

    No shape is checked here, because the caller (`reach`) counts CALLS and
    does not keep the argument names. Asking a schema about an empty
    argument list would answer "required parameter missing" and file a live
    verb as broken - which is exactly the false alarm this module exists to
    remove, reintroduced one layer down.
    """
    if verb not in ENGINE_SIDE_VERBS:
        return None
    schemas = engine_tool_schemas()
    if schemas is None:
        return (True, "registered engine-side by pinned_boot (engine/src/sim); the engine's "
                      "interpreter could not be run here to confirm it today")
    if verb in schemas:
        return (True, "registered engine-side by pinned_boot (engine/src/sim), not by the "
                      "world registry this audit reads")
    return (False, "pinned_boot no longer registers this verb onto the model's registry")


def resolve(verb: str, params: Sequence[str]) -> tuple[str, str] | None:
    """How a verb the world registry does not serve should be judged.

    Returns `(verdict_hint, sentence)` where the hint is "proven", "failed"
    or "unproven", or None when this verb is not an engine-side one at all
    (in which case the caller's own "gone" reading stands).
    """
    if verb not in ENGINE_SIDE_VERBS:
        return None
    schemas = engine_tool_schemas()
    if schemas is None:
        return ("unproven", "served engine-side by pinned_boot; the engine's "
                            "interpreter could not be run here, so the shape was not checked")
    schema = schemas.get(verb)
    if schema is None:
        return ("failed", "pinned_boot no longer registers this verb onto the model's registry")
    why = shape_fits(schema, params)
    if why is None:
        return ("proven", "served engine-side by pinned_boot (engine/src/sim), and the "
                          "engine's own schema still accepts this spelling")
    return ("failed", f"served engine-side by pinned_boot, and the engine's schema now refuses it: {why}")
