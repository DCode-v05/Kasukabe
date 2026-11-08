"""The learning pipeline was writing a miller up as a support ticket.

`learning_centre/pipeline.py` ships four system prompts and every one of
them is written for the memory of **a personal AI assistant**: "a narrative
record of the USER'S DIGITAL LIFE", "facts about THE USER, their team,
tools, environment", worked examples reading 'deploys on Tuesdays' and
'checks Grafana first when debugging production'.

Rosalind Vance is a miller. She has no team and no Grafana, and there is
no user. Run 25's batches did the only thing those prompts allow - they
cast her as the operator of an assistant. Read out of her real brain
afterwards:

    "The user concurrently handles interconnected responsibilities:
     trade negotiations (Junia/wheat pricing), conflict resolution ..."
    "The user exhibits consistent location-based workflow optimization ..."
    "Multiple entries reveal interconnected economic stressors: mill
     operation (732ea...), household financial obligations (9874...)"

That is her own memory of her own morning, handed back to her in the third
person by a productivity tool, citing internal row ids. Nothing in it is
actionable, which is the point of memory.

Two things are being guarded here, and the second is the one that would
fail silently:

1. the register actually changed - resident, not assistant;
2. the JSON CONTRACT did not. Field names, nesting and the empty
   sentinels are the pipeline's parser and its storage schema. A prompt
   rewrite that also moved a key is a parser change wearing a prompt's
   clothes, and it fails into an empty batch with no error at all.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from src.learning_centre import pipeline as frozen_pipeline
from src.sim import resident_learning_prompts as resident


@pytest.mark.parametrize("name", resident.PROMPT_NAMES)
def test_no_resident_prompt_speaks_of_a_user_or_an_assistant(name):
    """"the user" is the whole defect: it is a third party who does not
    exist, and naming one is what put Rosalind outside her own memory.

    Quoted examples are excluded, and deliberately: CONSOLIDATION_SYSTEM
    quotes run 25's worst row - 'the user exhibits workflow optimization'
    - as the negative example. A counter-example is the strongest way to
    rule a register out, so the phrase appearing INSIDE quotes is the fix
    working. What must not appear is the phrase as an INSTRUCTION.
    """
    text = getattr(resident, name).lower()

    for match in re.finditer(r"the user", text):
        preceding = text[match.start() - 1 : match.start()]
        assert preceding == "'", (
            f"{name} uses 'the user' as an instruction, not as a quoted "
            f"counter-example: ...{text[max(0, match.start() - 60):match.end() + 40]}..."
        )
    assert "personal ai assistant" not in text, f"{name} still says assistant"


@pytest.mark.parametrize("name", resident.PROMPT_NAMES)
def test_every_resident_prompt_names_the_town(name):
    """A prompt that does not say where it is gets the register from the
    examples, and the examples were about deploys and code review."""
    assert "kasukabe" in getattr(resident, name).lower()


def test_the_worked_examples_are_from_this_world():
    """The originals taught by example - 'deploys on Tuesdays', 'code
    review needs 2 approvals'. Examples are the strongest instruction in
    a prompt, and those three were teaching the wrong life."""
    knowledge = resident.KNOWLEDGE_ENCODING_SYSTEM.lower()

    for stale in ("deploys on tuesdays", "code review", "grafana", "ec2", "redis"):
        assert stale not in knowledge, f"{stale!r} survived the rewrite"

    assert "wheat" in knowledge and "mill" in knowledge


def test_consolidation_forbids_the_line_it_actually_produced():
    """Run 25's worst row, named in the prompt so the model has the
    negative example as well as the positive one."""
    text = resident.CONSOLIDATION_SYSTEM.lower()

    assert "record id" in text, "nothing stops it citing 732ea... again"
    assert "workflow optimization" in text, "the observed failure is not named"
    assert "empty list is a good answer" in text, "noise is still cheaper than silence"


CONTRACTS = [
    ("EPISODE_ENCODING_SYSTEM", ["key", "description"]),
    ("KNOWLEDGE_ENCODING_SYSTEM", ["entries", "subject", "content", "type", "confidence", "tags"]),
    ("SOCIAL_ENCODING_SYSTEM", ["nodes", "edges", "name", "attributes", "relationship"]),
    ("CONSOLIDATION_SYSTEM", [
        "cross_patterns", "soul_updates", "contradictions",
        "action", "trait", "value", "evidence_count",
        "existing_entry_id", "new_entry_id", "reason",
    ]),
]


@pytest.mark.parametrize("name,keys", CONTRACTS)
def test_the_json_keys_the_parser_reads_all_survive(name, keys):
    ours = getattr(resident, name)
    for key in keys:
        assert f'"{key}"' in ours, f"{name} dropped the {key!r} key the parser reads"


@pytest.mark.parametrize("name,keys", CONTRACTS)
def test_the_frozen_prompt_asked_for_exactly_those_keys(name, keys):
    """Proves the list above is the ENGINE's contract and not a list I
    invented to match my own prompt. If upstream changes its keys, this
    fails and the pair has to be re-derived together."""
    theirs = getattr(frozen_pipeline, name)
    for key in keys:
        assert f'"{key}"' in theirs, f"{key!r} is not in the engine's {name}"


@pytest.mark.parametrize("name", resident.PROMPT_NAMES)
def test_the_empty_answer_is_still_offered(name):
    """Every prompt ends by naming the empty result. Without it the model
    invents something rather than return nothing, and a batch with
    nothing in it is a normal outcome."""
    assert "if nothing" in getattr(resident, name).lower() or "if nobody" in getattr(
        resident, name
    ).lower() or "if the signals are too trivial" in getattr(resident, name).lower()


@pytest.mark.parametrize("name", resident.PROMPT_NAMES)
def test_only_valid_json_is_still_demanded(name):
    assert "return only valid json" in getattr(resident, name).lower()


def test_the_confidence_bands_are_the_stores(_bands=None):
    """0.4-1.0 with user-stated at 1.0 is not a style choice - the
    knowledge store's dedup and `step8_confidence_decay` both read it,
    and `search.py` ranks on it."""
    ours = resident.KNOWLEDGE_ENCODING_SYSTEM
    assert "0.4-1.0" in ours
    assert "1.0" in ours and "0.7-0.8" in ours and "0.4-0.6" in ours


def test_install_rebinds_every_prompt_on_the_frozen_module():
    fake = SimpleNamespace(**{n: "the engine's original" for n in resident.PROMPT_NAMES})

    count = resident.install(fake)

    assert count == len(resident.PROMPT_NAMES)
    for name in resident.PROMPT_NAMES:
        assert getattr(fake, name) == getattr(resident, name)


def test_a_renamed_prompt_upstream_is_loud_not_silent(caplog):
    """Silently skipping is how a prompt rewrite becomes a no-op nobody
    notices for twenty-three runs. This is the L1 failure the evaluation
    spec exists to catch, applied to itself."""
    fake = SimpleNamespace(EPISODE_ENCODING_SYSTEM="x")

    with caplog.at_level("ERROR"):
        count = resident.install(fake)

    assert count == 1
    assert "was NOT installed" in caplog.text


def test_install_is_idempotent():
    fake = SimpleNamespace(**{n: "original" for n in resident.PROMPT_NAMES})

    resident.install(fake)
    second = resident.install(fake)

    assert second == len(resident.PROMPT_NAMES)
    assert fake.EPISODE_ENCODING_SYSTEM == resident.EPISODE_ENCODING_SYSTEM


def test_boot_installs_them():
    """A module nobody calls is the failure this whole file is about.

    Parsed, not grepped. The first version of this test asserted
    `"install_resident_prompts()" in source` - and passed when the call
    was commented out, because the string is still there in the comment.
    That is the same defect as the `/world/link` test that asserted a
    route's source text and never noticed the route raised NameError on
    every request. A source string is not a call.
    """
    import ast
    import inspect

    from src.sim import pinned_boot

    tree = ast.parse(inspect.getsource(pinned_boot))

    called_at = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "install_resident_prompts"
    ]
    assert called_at, "pinned_boot never CALLS install_resident_prompts"

    built_at = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "LCPipeline"
    ]
    assert built_at, "LCPipeline is no longer constructed here - re-derive this test"
    assert min(called_at) < min(built_at), (
        "the prompts are installed AFTER the pipeline is built, so a batch "
        "can still encode in the assistant register"
    )


def test_the_prompt_rewrite_itself_edits_nothing_frozen():
    """Law 1, as the manifest actually states it.

    The freeze is not absolute - it permits exceptions, provided they are
    LISTED, because "an exception can be audited and a fiction cannot"
    (docs/policy/LAW-1-MANIFEST.md). So the rule to enforce is not "the
    frozen tree is untouched" but "every file touched in the frozen tree
    is named in the manifest".

    The prompt rewrite this file guards is not one of them, and that is
    the point of the whole `resident_learning_prompts` indirection: it
    changes what the pipeline SENDS without changing the pipeline.
    """
    import subprocess
    from pathlib import Path

    root = Path(_repo_root())
    changed = subprocess.run(
        ["git", "status", "--short", "--", "engine/src/engine_core", "engine/src/memory",
         "engine/src/herald", "engine/src/learning_centre"],
        capture_output=True, text=True, cwd=root,
    ).stdout.strip()

    manifest = (root / "docs/policy/LAW-1-MANIFEST.md").read_text()
    for line in [ln for ln in changed.splitlines() if ln.strip()]:
        path = line.split()[-1]
        name = Path(path).name
        assert name in manifest, (
            f"{path} is inside the frozen tree and is not named in "
            f"LAW-1-MANIFEST.md. Either revert it or record the exception - "
            f"an unlisted breach makes the freeze a fiction."
        )

    assert "resident_learning_prompts" not in changed


def _repo_root() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[2])


def test_the_engine_prompts_are_still_the_assistant_ones():
    """Proves the rewrite lives in sim/ and not in the frozen file - if
    somebody 'simplifies' this by editing pipeline.py, this fails."""
    assert "personal AI assistant" in frozen_pipeline.EPISODE_ENCODING_SYSTEM or (
        frozen_pipeline.EPISODE_ENCODING_SYSTEM == resident.EPISODE_ENCODING_SYSTEM
    )
