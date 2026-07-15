"""
gpi — the deterministic + Anthropic-batch core of the Gene Program Interpreter.

Vendored and generalized from ProgExplorer's `pipeline/` (read-only source),
standardized on the Anthropic API (no Vertex / AI-gateway backends).

Modules are importable as a package (e.g. ``from gpi.column_mapper import ColumnMapper``)
and the ones with a CLI are runnable as ``python -m gpi.<module> ...``.
"""

__version__ = "0.2.3"
