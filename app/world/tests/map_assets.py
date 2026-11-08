"""Whether this host has the Ville map assets, and how to say so.

`TOOL-27`. The assets live in the sibling `smallville/` checkout, are
gitignored, and are absent on any host that did not clone them. Without
them roughly a hundred tests across `world/tests/` and
`world/tools/tests/` FAILED rather than skipped - because
`build_world_server` and `build_world` both load the map - and only
`test_construction.py` skipped honestly.

Red for environment is worse than red for nothing: it hides real
regressions inside the noise, which is how TOOL-09 stayed invisible for
a whole review cycle.

INFRA-30's rule is why this SKIPS rather than quietly passing: a host
precondition is not a result, and a suite that went green without the
map would be claiming coverage it does not have. The runner's own
skip summary prints the reason, so a green run says which.
"""

from __future__ import annotations

import pytest

from world.tools.bootstrap import DEFAULT_VILLE_DIR

MAP_ASSETS_PRESENT = DEFAULT_VILLE_DIR.is_dir()

NO_MAP_REASON = (
    f"the Ville map assets are not on this host ({DEFAULT_VILLE_DIR}); they "
    "live in the sibling smallville/ checkout, are gitignored, and are a "
    "host precondition rather than a result (INFRA-30, TOOL-27)"
)

# Applied per MODULE as `pytestmark = needs_the_map`, never to a shared
# world-building fixture: most tests using those fixtures do not touch
# the map, and skipping there would hide hundreds of real passes behind
# one absent directory.
needs_the_map = pytest.mark.skipif(not MAP_ASSETS_PRESENT, reason=NO_MAP_REASON)
