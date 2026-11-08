"""Product-layer services that sit above the engine core.

Modules here compose engine primitives (ActivityManager, Herald, Raven) into
user-facing capabilities.  Unlike ``utils/`` these services may depend on
the LLM adapter and other boot-time subsystems, so they are lazy-imported
from consumers to keep module import graphs clean.
"""
