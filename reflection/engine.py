"""Deprecated beta3 compatibility entrypoint for runtime reflection.

The canonical implementation is ``mem_reflection_hermes.runtime_reflection``.
This module exists only so explicit legacy imports keep resolving while still
sharing the runtime globals used by older monkeypatch-based consumers.
"""
from __future__ import annotations

from pathlib import Path

_runtime_path = Path(__file__).resolve().parent.parent / "runtime_reflection.py"
_compat_name = __name__
_compat_package = __package__.rsplit(".", 1)[0] if __package__ and "." in __package__ else __package__

globals()["__file__"] = str(_runtime_path)
globals()["__name__"] = _compat_name
globals()["__package__"] = _compat_package
globals()["__spec__"] = None

exec(compile(_runtime_path.read_text(encoding="utf-8"), str(_runtime_path), "exec"), globals())
