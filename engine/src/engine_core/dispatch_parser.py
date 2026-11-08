"""Dispatch parsing - turn the triage model's JSON into a validated Dispatch.

The model proposes; this module disposes. Everything it returns is treated as
untrusted: ids are normalized against the real index and dropped when unknown,
verdicts are checked against the vocabulary, shape rules are enforced per
verdict, and every extract must be a verbatim cut of the input.

The verbatim rule is the load-bearing one. A segment's extract becomes the text
its thread receives, so a paraphrase would silently rewrite what the user said
before any thread saw it, and invented text would be a way to smuggle new
instructions into a thread. An extract that is not found in the input is
replaced with the full input rather than trusted.
"""

from __future__ import annotations

import logging
import re

from src.engine_core.schema import (
    TARGETED_VERDICTS,
    VERDICTS,
    Dispatch,
    DispatchSegment,
)
from src.engine_core.stop_hooks import extract_json_object

logger = logging.getLogger(__name__)


def normalize_whitespace(text: str) -> str:
    """Collapse whitespace and case for verbatim comparison. Models reflow
    line breaks and spacing when quoting; that is not paraphrase."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def is_verbatim(extract: str, message: str) -> bool:
    """True when the extract is a contiguous cut of the input, ignoring
    whitespace reflow and case."""
    if not extract:
        return False
    return normalize_whitespace(extract) in normalize_whitespace(message)


def resolve_thread_id(candidate: str | None, known_ids: set[str]) -> str | None:
    """Match a model-supplied id against the real index.

    Exact match wins. Failing that, a unique suffix match is accepted: the
    model routinely emits ``f024`` for ``thread-f024``, and rejecting that
    would spawn a duplicate thread for work that already has one. Ambiguous
    or unknown ids resolve to None.

    A non-string candidate resolves to None rather than raising. The suffix
    shorthand this function exists to forgive is exactly what produces one: an
    all-digit hex suffix comes back as a bare JSON number (``"target": 8325``),
    and ``target`` shares an object with the array-valued ``related_ids`` so
    ``"target": ["thread-a41b"]`` is a routine model slip too. Both used to hit
    ``.strip()`` and raise inside a function documented never to raise, and the
    caller's try/except read that as a provider blip and burned a retry.

    It must REJECT rather than coerce: ``str(8325)`` suffix-matches any id
    ending in 8325 and would misroute silently, where None degrades to a new
    thread - wrong, but wrong in the direction the shape rules already handle.
    """
    if not isinstance(candidate, str):
        if candidate is not None:
            # Distinguishable in the logs from an id that simply named no
            # thread: this one was the wrong TYPE, which points at the prompt.
            logger.warning(
                "dispatch: non-string thread id %r (%s) - treating as unresolved",
                candidate,
                type(candidate).__name__,
            )
        return None
    if not candidate:
        return None
    candidate = candidate.strip()
    if candidate in known_ids:
        return candidate
    matches = [tid for tid in known_ids if tid.endswith(candidate) or candidate.endswith(tid)]
    if len(matches) == 1:
        logger.info("dispatch: normalized thread id %r to %r", candidate, matches[0])
        return matches[0]
    if matches:
        logger.warning("dispatch: ambiguous thread id %r matched %d threads", candidate, len(matches))
    return None


def single_segment(
    message: str,
    verdict: str = "open",
    reasoning: str = "",
    target: str | None = None,
) -> Dispatch:
    """The whole input as one piece of work - the shape every bypass produces.

    It is the safe answer when triage cannot be trusted: under-splitting loses
    nothing, since the thread still sees every word, while a bad split would
    scatter it. It is also what the structural bypasses build, so this is the
    one place the shape is defined and a new field cannot reach some callers
    and miss others.
    """
    return Dispatch(
        segments=[DispatchSegment(extract=message, verdict=verdict, target=target)],
        reasoning=reasoning,
    )


def parse_dispatch(raw: str, message: str, known_ids: set[str]) -> Dispatch:
    """Validate a triage response into a Dispatch. Never raises.

    Malformed responses degrade to a single ``open`` segment carrying the whole
    input, which is always a legal thing to do with an input.
    """
    parsed = extract_json_object(raw)
    if not isinstance(parsed, dict):
        logger.warning("dispatch: unparseable response: %s", (raw or "")[:200])
        return single_segment(message, reasoning="unparseable dispatch response")

    raw_segments = parsed.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        logger.warning("dispatch: no segments in response")
        return single_segment(message, reasoning="dispatch returned no segments")

    reasoning = str(parsed.get("reasoning", "") or "")
    segments: list[DispatchSegment] = []

    for item in raw_segments:
        if not isinstance(item, dict):
            continue

        verdict = str(item.get("verdict", "") or "").strip().lower()
        if verdict not in VERDICTS:
            logger.warning("dispatch: unknown verdict %r - treating as open", verdict)
            verdict = "open"

        extract = str(item.get("extract", "") or "")
        if not is_verbatim(extract, message):
            # Not the user's words. Hand the thread the whole input rather
            # than text the model composed.
            logger.warning(
                "dispatch: non-verbatim extract %r - falling back to full input",
                extract[:80],
            )
            extract = message

        target = resolve_thread_id(item.get("target"), known_ids)
        # Resolve, drop what does not name a real thread, dedupe - in one pass,
        # with a dict as the ordered set so first mention wins.
        seen: dict[str, None] = {}
        for rid in item.get("related_ids") or []:
            if not isinstance(rid, str):
                continue
            resolved = resolve_thread_id(rid, known_ids)
            if resolved:
                seen[resolved] = None
        related = list(seen)

        # Shape rules. A verdict that has lost the thread it depends on is
        # downgraded rather than dropped: the work still needs a home.
        if verdict in TARGETED_VERDICTS and target is None:
            logger.warning("dispatch: %s without a known target - downgrading to open", verdict)
            verdict = "open"
        if verdict in ("claimed", "contested") and not related:
            logger.warning("dispatch: %s without known related ids - downgrading to open", verdict)
            verdict = "open"
        if verdict in ("open", "status"):
            target = None
        if verdict not in ("claimed", "contested"):
            related = []

        title = str(item.get("title", "") or "").strip() if verdict == "open" else ""

        segments.append(
            DispatchSegment(
                extract=extract,
                verdict=verdict,
                target=target,
                title=title,
                related_ids=related,
            )
        )

    if not segments:
        return single_segment(message, reasoning="no usable segments in dispatch")

    return Dispatch(segments=segments, reasoning=reasoning)
