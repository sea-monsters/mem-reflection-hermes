"""Deprecated beta3 compatibility alias for the runtime tool module.

The canonical implementation is ``mem_reflection_hermes.runtime_tools``.
This module exists only so explicit legacy imports keep resolving.
"""
from __future__ import annotations

import importlib
import sys

try:
    _runtime_tools = importlib.import_module("..runtime_tools", __package__)
except ImportError:
    try:
        _runtime_tools = importlib.import_module("mem_reflection_hermes.runtime_tools")
    except ImportError:
        _runtime_tools = importlib.import_module("runtime_tools")

sys.modules[__name__] = _runtime_tools
