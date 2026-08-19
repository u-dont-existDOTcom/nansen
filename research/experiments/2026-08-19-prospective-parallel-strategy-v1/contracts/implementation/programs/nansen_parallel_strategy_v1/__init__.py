"""Independent prospective parallel-strategy discovery/validation protocol.

The package is intentionally separate from the frozen v1, A, and A2 runtimes.
Importing it performs no filesystem or provider access.
"""

from .design import PROGRAM_ID

__all__ = ["PROGRAM_ID"]
