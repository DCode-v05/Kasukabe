"""One module per family of DESIGN.md section 2 primitives. Every module
here exports a `build_specs() -> list[ToolSpec]` function; bootstrap.py
calls each and registers the result, so adding a new primitive to an
existing family never touches bootstrap.py itself.
"""

from __future__ import annotations
