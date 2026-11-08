"""Bundle the town's rulebook for the browser.

Regenerate: python3 game/static/js/township/build_rules.py

`world/rules/village_rules.json` is the one canonical rulebook - the world
server loads and validates it, and `GET /api/rules` serves it. The township
page also needs it, and cannot always fetch: `township-v2.html` is openable
straight off disk (docs/HANDOVER.md §1, Route A), and `fetch()` cannot read a
`file://` JSON path. The same problem `build_characters.py` solves for the
cast, solved the same way - the data is bundled as a JS constant.

So there are two copies of the rules and exactly one source. This script is
the only thing that writes the JS one, the validator runs over the JSON one
before it is ever bundled, and `test_village_rules.py` fails if the two drift.
Editing the JS copy by hand is always wrong; edit the JSON and re-run this.

When the page IS served, township-v2.js still re-fetches `/api/rules` on load
and prefers what comes back, so a rule edited on a running server takes effect
on reload without regenerating anything. The bundle is the floor, not the
ceiling.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parents[3]
RULES_JSON = APP / "world" / "rules" / "village_rules.json"
OUT_JS = HERE / "rules.js"

BANNER = """\
// The town's rulebook, bundled for the browser. GENERATED - DO NOT EDIT.
//   source:      world/rules/village_rules.json
//   regenerate:  python3 game/static/js/township/build_rules.py
//
// Edit the JSON, not this file. The JSON is what the world server loads and
// validates; this is a copy so the page also works opened straight off disk,
// where fetch() cannot read a file:// path. When the page is served,
// township-v2.js re-fetches /api/rules and prefers that, so a rule changed on
// a running server needs no regeneration.
"""


def main() -> int:
    if not RULES_JSON.exists():
        print(f"no rulebook at {RULES_JSON}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(APP))
    try:
        from world.rules import RuleError, load_rules
    except ImportError as exc:                       # pragma: no cover
        print(f"cannot import the validator: {exc}", file=sys.stderr)
        return 1
    try:
        load_rules(RULES_JSON, reload=True)
    except RuleError as exc:
        print(f"refusing to bundle an invalid rulebook:\n{exc}", file=sys.stderr)
        return 1

    data = json.loads(RULES_JSON.read_text(encoding="utf-8"))
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    OUT_JS.write_text(
        BANNER + "const VILLAGE_RULES = " + body + ";\n", encoding="utf-8"
    )
    goods = [k for k in data["market"]["goods"] if not k.startswith("_")]
    print(f"wrote {OUT_JS.relative_to(APP)}  ({len(body):,} bytes, {len(goods)} goods)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
