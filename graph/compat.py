"""Deprecated beta3 compatibility alias for the runtime graph module.

The canonical implementation is ``mem_reflection_hermes.runtime_graph``.
This module exists only so explicit legacy imports keep resolving.
"""
from __future__ import annotations

import importlib
import sys

try:
    _runtime_graph = importlib.import_module("..runtime_graph", __package__)
except ImportError:
    try:
        _runtime_graph = importlib.import_module("mem_reflection_hermes.runtime_graph")
    except ImportError:
        _runtime_graph = importlib.import_module("runtime_graph")

sys.modules[__name__] = _runtime_graph
