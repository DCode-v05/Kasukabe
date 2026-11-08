"""A building the town raised must still be open after a resume.

`GROW-15`. `MapService.stamp_building` names a built sector
`f"{purpose} ({owner})"` - "smithy (n15)". `MapService.restore` then calls
`apply_genesis_config`, which classifies every sector through
`classify_sector`; "smithy (n15)" matches no entry in
`KNOWN_SECTOR_CATEGORIES` and no keyword in `FALLBACK_KEYWORD_RULES`, so
it gets an empty category set, and an unclassified sector is `active=False`
by design.

`location_api` filters on `sector.active`. So the town could build a
smithy, walk to it, forge in it - and the first checkpoint resume would
quietly close it, with no error anywhere.

The four locks on building came off in this review's own pass (GROW-01 to
GROW-04). This is the fifth, and it only appears once something is
actually built, which is why nobody hit it before.
"""
from __future__ import annotations

import pytest

from world.map.genesis import classify_sector
from world.tools.locations import WORKSHOPS_BY_BUSINESS_TYPE

BUILDABLE_PURPOSES = sorted(
    {workshop for workshops in WORKSHOPS_BY_BUSINESS_TYPE.values() for workshop in workshops}
    | {"hospital", "police_station", "well", "school", "granary"}
)


@pytest.mark.parametrize("purpose", BUILDABLE_PURPOSES)
def test_a_built_sector_classifies(purpose: str) -> None:
    """The name `stamp_building` actually writes, not the bare purpose."""
    categories = classify_sector(f"{purpose} (n15)")
    assert categories, (
        f"GROW-15: a built {purpose!r} classifies as nothing, so it is "
        "inactive on restore and location_api stops listing it"
    )


def test_the_owner_suffix_does_not_defeat_classification() -> None:
    """The suffix is what made this invisible: the bare purpose was never
    the string being classified."""
    assert classify_sector("smithy (n15)") == classify_sector("smithy")


def test_an_unknown_name_still_classifies_as_nothing() -> None:
    """The guard on the guard. Making everything classify would make
    `active` meaningless, which is worse than the bug."""
    assert not classify_sector("Ainsley's imaginary observatory")
