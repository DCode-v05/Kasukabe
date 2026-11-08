"""World server package.

Owns ALL world state: map, ticks, tool registry, ledger, charter, health,
crime, genesis generator (DESIGN.md section 9). No other package in this
monorepo may mutate world state directly - they go through this package's
async API or HTTP surface (see app/CONVENTIONS.md, "How modules talk").

This package must never import boto3. Model inference is broker/'s job.
"""

from __future__ import annotations
