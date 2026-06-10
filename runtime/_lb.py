"""Shared late-binding helper for runtime modules."""
from __future__ import annotations


def _lb(name: str):
    """Resolve a symbol from the mem_reflection_hermes package by name.

    Used by runtime modules (tools, hooks) to avoid circular imports —
    they delegate to package-level functions registered in __init__.py.
    """
    from mem_reflection_hermes import __dict__ as _pkg_dict
    return _pkg_dict[name]
