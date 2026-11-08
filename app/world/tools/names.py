"""A resident may name the person it means, and be answered in names.

Runs 28 and 28b addressed people by number because a person parameter took
only an agent id. In run 28b 367 tool-call parameters named someone by id and
42 of those calls failed - 11%, against 5% for names. A resident thinks in
names ("Marta Thorne", "Tova"), reads names in every perception, and was
answered in numbers ("no agent with id 'Tova Okonjo'").

Both halves of that live here, because they are one fact - the town has two
words for every person and something has to join them.

**Inbound.** Before a call is validated, and for every tool at once, a person
parameter given as a NAME becomes the id:

  * a full name, case and spacing ignored;
  * otherwise a first name that exactly one resident has;
  * otherwise, if several share it and exactly one is standing with the
    caller, that one;
  * and "Marta Thorne (n07)" - the exact form the world hands back below -
    is the id in the brackets, so what the town said can be repeated to it.

Two refusals, both naming people rather than numbering them: a name several
residents share (listing them), and a name nobody has (listing who is here).

Deliberately narrow. A value that is already an agent id, or a business the
town knows, is left alone; so is any value SHAPED like an id (lowercase, no
spaces) that matches nobody - the tool's own "not found" still answers it, as
every existing refusal and test expects.

**Outbound**, and this is what runs 28 and 28b were still missing. Fixing only
the inbound half left the town speaking two languages and joining them
nowhere:

  * the perception has always said `Marta Thorne (n07)` -
    `retrieval_depth._named`, "both halves, always";
  * the once-written roster says `Marta Thorne (baker)` - a name and a trade,
    no id at all;
  * every tool result and every refusal said `n07`, and nothing else the
    resident had ever read attached that token to a person.

So a resident that looked around a room read `people_here: ["n02"]` - a token
with no name on it anywhere in its context - and acted on it. Run 28b, Silas
Vance at the mill, verbatim: *"Someone is here - n02, who..."* and then
`say(content="You there. I have planks...")`, addressed to a stranger he had
been living beside since genesis. Marta's refusal read *"n07 needs 1 bread,
has 0"* to a buyer who had asked for Marta.

`name_the_numbers` is the join, applied once at the world's edge: every agent
id the world hands a resident - in a result, in a refusal, in the standing
brief - is written the way the perception has always written it. It runs on
the way OUT only. Programs binding a step result, the evals, the trace and the
game all read the same raw ids they always did.
"""

from __future__ import annotations

import re
from typing import Any

from world.tools.errors import NotFoundError

_HOLDER_FIELDS = frozenset({"seller_id", "buyer_id", "to_owner_id"})

_PERSON_FIELDS = frozenset({"licensee_id", "student_id", "employee_id"})

_ID_SHAPED = re.compile(r"^[a-z0-9_.:-]+$")

_LABELLED = re.compile(r"^(?P<name>.+?)\s*\((?P<id>[A-Za-z0-9_.-]+)\)$")


def _squash(text: str) -> str:
    return " ".join(str(text).lower().split())


def label(world: Any, agent_id: str) -> str:
    """"Marta Thorne (n07)" - the one way the town writes a person.

    The same shape `retrieval_depth._named` has always used in the
    perception, and the reason it gives holds everywhere else too: the name
    is what a resident can address and remember, the id is what every person
    parameter still accepts.
    """
    return f"{world.agent_names.get(agent_id, agent_id)} ({agent_id})"


_label = label


def _is_person_field(name: str) -> bool:
    return name.endswith("agent_id") or name in _HOLDER_FIELDS or name in _PERSON_FIELDS


def people_named(world: Any, reference: str, near: str | None = None) -> list[str]:
    """Agent ids a name could mean, best match first. Empty when none."""
    wanted = _squash(reference)
    names = {agent_id: _squash(name) for agent_id, name in (world.agent_names or {}).items()
             if agent_id in world.agents}
    full = [agent_id for agent_id, name in names.items() if name == wanted]
    if full:
        return full
    first = [agent_id for agent_id, name in names.items() if name.split(" ", 1)[0] == wanted]
    if len(first) > 1 and near is not None:
        here = [agent_id for agent_id in first if world.location_of(agent_id) == near]
        if len(here) == 1:
            return here
    return first


def resolve_person_references(world: Any, actor_id: str, tool_name: str, fields: Any,
                              params: dict[str, Any]) -> dict[str, Any]:
    """`params` with every person named by name rewritten to their agent id."""
    from world.tools.place_aliases import resolve_business

    resolved = dict(params)
    near = world.location_of(actor_id)
    for key, value in params.items():
        if key not in fields or not _is_person_field(key) or not isinstance(value, str):
            continue
        reference = value.strip()
        if not reference or reference in world.agents:
            continue
        labelled = _LABELLED.match(reference)
        if labelled and labelled.group("id") in world.agents:
            resolved[key] = labelled.group("id")
            continue
        if key in _HOLDER_FIELDS and resolve_business(world, reference) is not None:
            continue
        matches = people_named(world, reference, near)
        if len(matches) == 1:
            resolved[key] = matches[0]
            continue
        if len(matches) > 1:
            raise NotFoundError(
                "resident", reference, [_label(world, m) for m in matches],
                hint=(
                    f"{tool_name}: {reference!r} could be "
                    f"{', '.join(_label(world, m) for m in matches)}. Give the full name."
                ),
            )
        if _ID_SHAPED.match(reference):
            continue
        if key in _HOLDER_FIELDS:
            continue
        here = sorted(
            agent_id for agent_id in world.agents
            if agent_id != actor_id and world.location_of(agent_id) == near
        )
        raise NotFoundError(
            "resident", reference, [_label(world, m) for m in here],
            hint=(
                f"{tool_name}: nobody in Kasukabe is called {reference!r}. "
                + (f"Here with you: {', '.join(_label(world, m) for m in here)}."
                   if here else "Nobody is here with you.")
            ),
        )
    return resolved


def _living_names(world: Any) -> dict[str, str]:
    """Agent id -> name, for residents that exist. Empty when none are named.

    Keyed on `world.agents` rather than on the name map alone: a name left
    behind by a resident who is gone is not somebody this town can be told
    about, and decorating a dead id would put a person back in the room.

    `world.agent_names` is read directly. It is a field of `WorldState` with a
    default, so a three-argument `getattr` here would only be able to hide a
    caller passing something that is not a world - and `test_no_silent_
    defaults` is in this repo because that is how a defect gets a long life.
    """
    return {
        agent_id: name
        for agent_id, name in (world.agent_names or {}).items()
        if name and agent_id in world.agents
    }


def _mention_pattern(known: dict[str, str]) -> re.Pattern[str]:
    """Every live id, longest first, as whole tokens only.

    The lookarounds keep `n07` inside `home:h_n07` or `n070` alone. Longest
    first because `re` alternation is first-match, not longest-match, and a
    town holding both `n1` and `n17` would otherwise have every `n17`
    rewritten as `n1` followed by a stray 7.
    """
    ids = sorted(known, key=len, reverse=True)
    return re.compile(
        r"(?<![0-9A-Za-z_.:-])(" + "|".join(re.escape(i) for i in ids) + r")(?![0-9A-Za-z_-])"
    )


def _swap_ids(known: dict[str, str], pattern: re.Pattern[str], text: str) -> str:
    def _swap(match: re.Match[str]) -> str:
        agent_id = match.group(1)
        name = known[agent_id]
        before = text[: match.start()].rstrip()
        if before.endswith("(") and before[:-1].rstrip().endswith(name):
            return agent_id
        return f"{name} ({agent_id})"

    return pattern.sub(_swap, text)


def name_mentions(world: Any, text: str) -> str:
    """One sentence with every agent id in it written as "Name (id)".

    Used on refusals, which are prose: *"n07 needs 1 bread, has 0"* is a
    true sentence about a person the buyer knows as Marta, and it was read
    18 times in run 28b by residents who could not tell it was about her.
    """
    known = _living_names(world)
    if not known or not text:
        return text
    return _swap_ids(known, _mention_pattern(known), text)


def name_the_numbers(world: Any, value: Any) -> Any:
    """Anything the world is about to hand a resident, with the names put back.

    Walks the structure and rewrites agent ids wherever they appear - as a
    whole string (`people_here: ["n02"]`), inside a sentence (a refusal), and
    as a dict key. A copy: the caller's own object is not touched, because
    the same result dict is what a program binds, what the trace stores and
    what the game draws, and every one of those wants the raw id.

    Conservative by construction. Only a string that IS a live agent id, or
    contains one as a whole token, changes at all; numbers, item names, place
    ids and business ids pass through untouched.

    The pattern is compiled once per call rather than once per string. A
    standing brief is a few dozen strings and a room's `look_around` a few
    hundred; rebuilding a 20-branch alternation for each was measurable in
    a tick and buys nothing.
    """
    known = _living_names(world)
    if not known:
        return value
    return _walk(known, _mention_pattern(known), value)


def _walk(known: dict[str, str], pattern: re.Pattern[str], value: Any) -> Any:
    if isinstance(value, str):
        return _swap_ids(known, pattern, value)
    if isinstance(value, dict):
        return {
            (_swap_ids(known, pattern, k) if isinstance(k, str) else k):
            _walk(known, pattern, v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_walk(known, pattern, item) for item in value]
    return value
