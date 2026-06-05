"""Graph package compatibility wrapper exposing GraphIndex."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_graph_path = Path(__file__).resolve().parent.parent / "graph.py"
_spec = importlib.util.spec_from_file_location(
    "mem_reflection_hermes.graph_runtime", str(_graph_path)
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load graph runtime from {_graph_path}")

_mod = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("mem_reflection_hermes.graph_runtime", _mod)
_spec.loader.exec_module(_mod)

GraphIndex = _mod.GraphIndex

__all__ = ["GraphIndex"]

