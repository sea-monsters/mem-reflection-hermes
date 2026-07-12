"""Shared late-binding helper for runtime modules.

Supports two resolution strategies transparently:
- Dotted module names (e.g. "core.store", "runtime.graph") are imported via
  importlib.import_module and the resulting module is cached and returned.
- Bare symbols (no dots) are resolved from the mem_reflection_hermes package
  __dict__ for backward compatibility with existing runtime/tools.py and
  runtime/hooks.py callers.

On any failure the helper returns None so callers can fail open.
"""
from __future__ import annotations

import importlib
import logging
import sys
from types import ModuleType
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CACHE: dict[str, Optional[Any]] = {}


def _lb(name: str) -> Optional[Any]:
    """Resolve a module by name or a bare symbol from the package namespace.

    - Any name is first attempted as a module import; on success the module is
      returned and cached.
    - If import fails and the name contains no dot, it is resolved as a symbol
      from ``mem_reflection_hermes`` for backward compatibility with existing
      callers in runtime/tools.py and runtime/hooks.py.
    - If import fails and the name contains a dot, ``None`` is returned.

    All failures are silent (debug log only) so callers remain fail-open.
    """
    if not name:
        return None

    # Reject syntactically invalid identifiers (e.g. names with spaces).
    # Valid PEP 0401 module names: dot-separated identifiers.
    if not all(part.isidentifier() for part in name.split(".")):
        return None

    if name in _CACHE:
        return _CACHE[name]

    try:
        value = importlib.import_module(name)
    except Exception as e:
        logger.debug("Late-bound module import of %r failed: %s", name, e)
        if "." in name:
            value = None
        else:
            # Hermes loads plugins under hermes_plugins.<name>, but some
            # import paths reference the bare package name.  Check both.
            pkg = sys.modules.get("mem_reflection_hermes")
            if pkg is None:
                pkg = sys.modules.get("hermes_plugins.mem_reflection_hermes")
            if pkg is None:
                logger.debug(
                    "Late-bound symbol lookup of %r failed: package not loaded", name
                )
                value = None
            else:
                value = getattr(pkg, name, None)

    _CACHE[name] = value
    return value
