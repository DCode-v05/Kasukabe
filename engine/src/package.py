"""Package root resolver — single source of truth for locating runtime data.

PACKAGE_ROOT points to the repository root in development or the installed
package root in pip deployments. All runtime data paths (migrations/, catalog/,
scripts/) resolve relative to this.
"""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
