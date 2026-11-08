"""Life systems: health, aging, marriage, birth, death, population, genesis.

Owns everything DESIGN.md section 5 ("Health & death") and the aging /
marriage / birth-flow bullets of section 4 describe, plus the GENESIS
GENERATOR that produces genesis.json (section 12: world/ owns "genesis
generator"). This is a sub-package of world/, so it inherits world/'s
module boundary from app/CONVENTIONS.md: it is part of the single writer
for world state, and it must never import boto3 (that is broker/'s job,
even for the "5k sim to god" blessing burn or the 10k sim revival fee -
this package only decides *that* a debit/credit should happen and calls
out through ports.EconomyPort; the actual ledger mutation, and the $1000
USD invariant check that goes with it, belongs to world's ledger module).

Every number in here that DESIGN.md does not pin exactly (pharmacy fee,
temple fee, chronic-hazard rate, elderly-hazard rate, work-capacity
multipliers, genesis starting sim balances) lives in constants.py with a
comment explaining it is a placeholder pending game-balance data, not a
transcription of a spec value. DESIGN.md's own numbers (the four ailment
incidence rates, the terminal death window, the 10k revival fee, the 5k
blessing fee - re-rated 2026-08-01 from the original 100k/50k figures,
the 24-hour dormancy timeout, the 18-year minority cutoff) are called
out as such right next to where they are used.
"""

from __future__ import annotations
