"""Compatibility package that forwards to top-level search.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_search_path = Path(__file__).resolve().parent.parent / "search.py"
_spec = importlib.util.spec_from_file_location("mem_reflection_hermes.search", str(_search_path))
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load search module from {_search_path}")

_mod = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("mem_reflection_hermes.search", _mod)
_spec.loader.exec_module(_mod)

for _name, _value in _mod.__dict__.items():
    if not _name.startswith("__"):
        globals()[_name] = _value

