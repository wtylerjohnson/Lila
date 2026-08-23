"""Shared intelligence graph interface and persistent computation cache.

This package is deliberately an adapter layer.  It does not replace the
retrieval, contact, classification, or rendering systems that already own
their data.  New consumers migrate behind this interface one at a time.
"""

from tools.intelligence_graph.adapter import ExistingSystemsGraphAdapter
from tools.intelligence_graph.cache import CacheResult, PersistentCache

__all__ = ["CacheResult", "ExistingSystemsGraphAdapter", "PersistentCache"]
