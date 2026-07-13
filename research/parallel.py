"""Alias module: ``python -m research.parallel`` -> ``research.research_parallel``.

The canonical implementation lives in ``research/research_parallel.py``; this thin
shim exists only so the CLI example ``python -m research.parallel ...`` also works.
"""

from __future__ import annotations

from research.research_parallel import *  # noqa: F401,F403
from research.research_parallel import main

if __name__ == "__main__":
    main()
