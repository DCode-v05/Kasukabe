"""The instrument, kept separate from the experiment.

Read-only, its own service on :8500, and it never writes to world, broker or
the game. That separation is the point: an observatory that could nudge the
town would stop being able to tell you what the town did on its own.

Three questions, in the order a researcher actually needs them:

1. **Is this run believable at all?** (`health.py`) A run at 39% dispatch
   success is a broken instrument, not a finding about society, and it must
   say so before anyone reads anything else off it.
2. **What can the town not do?** (`gaps.py`) Refused intentions, grouped by
   what was missing, with a headcount. The one view that turns "something is
   wrong" into a named thing to invent.
3. **What is failing, and where?** (`hops.py`) Failures by which link broke -
   bedrock, ledger, world, engine, broker-internal - rather than by HTTP
   status, which spent a whole run pointing at the wrong system.
"""
