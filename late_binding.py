"""Shared helpers for mem-reflection-hermes submodules."""
from __future__ import annotations

import sys
import threading
from typing import Any, Dict

_late_bindings: Dict[str, Any] = {}
_late_bindings_lock = threading.Lock()


def late_bind(name: str) -> Any:
    """Resolve a symbol from the root plugin module with a shared cache."""
    fn = _late_bindings.get(name)
    if fn is not None:
        return fn
    with _late_bindings_lock:
        fn = _late_bindings.get(name)
        if fn is not None:
            return fn
        mod = (sys.modules.get("hermes_plugins.mem_reflection_hermes")
               or sys.modules.get("mem_reflection_hermes"))
        if mod is None:
            raise KeyError("Plugin module not loaded for late binding: "
                           "tried hermes_plugins.mem_reflection_hermes "
                           "and mem_reflection_hermes")
        fn = getattr(mod, name, None)
        if fn is None:
            raise KeyError(f"Root plugin module has no attribute: {name}")
        _late_bindings[name] = fn
        return fn


def invalidate_late_bindings(names: list[str] | None = None) -> None:
    """Clear cached late bindings. Pass names to clear specific entries, or None for all."""
    with _late_bindings_lock:
        if names is None:
            _late_bindings.clear()
        else:
            for n in names:
                _late_bindings.pop(n, None)
