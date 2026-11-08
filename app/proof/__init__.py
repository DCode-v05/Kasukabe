"""`proof` - an independent instrument for deciding what this town has
actually done.

WHY A SECOND INSTRUMENT. The suite is large and green, and the town still
does things nobody intended. Three weeks of evidence say the dominant
defect class here is not "the code is wrong" but "the code is never
reached":

  - GOV-3: an elected government held its seats for a whole day and used
    no government tool. Not refused - `registry.visible_to` gates the
    GOVERNMENT namespace on `is_sitting` alone, so all eighteen verbs were
    in the winners' schemas wherever they stood, and the refusal table
    carries not one government entry. They were served and never reached.
  - `toolify`: 0 calls across runs 25, 26, 27, 28 and 28b, against 2,377 /
    536 / 521 / 1,225 / 885 resident calls.
  - The head-tax gate compared households VISITED against households that
    exist, and passed in all 23 runs against a tax of 0 bps that took
    nothing from anybody.

Every one of those is a true statement about code that does not run. A
suite of unit tests cannot see them, because each unit is correct.

THE RULE THIS PACKAGE IS BUILT ON.

    Nothing is green that never ran.

A mechanism no run ever exercised is reported `unproven`. It is never
reported as passing, and `unproven` is not a synonym for `failed` - it
means the instrument declines to score a thing it has no evidence about.
That distinction is the whole point: a green line over nothing is worse
than a red one, because it ends the search.

WHAT THIS IS NOT. It does not import, extend or run `world/evals/`, and
it carries nothing over from `docs/review/`. Those are read as
intelligence about where defects hide. The judging here is its own.

It also refuses two shapes this repository has been burned by:

  - No eval asserts on source text, function names, or the output of
    `inspect.getsource`. A comment naming a function must never satisfy a
    check that the function runs.
  - No eval runs against a stubbed ledger or an in-memory trace. Only the
    model is ever faked. An evaluation whose ledger is a fake can prove
    only that the fake agrees with itself.
"""
