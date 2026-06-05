"""
memory_bridge — Bidirectional sync between built-in memory and plugin memory.

Layers:
  Dir A (built-in → plugin): mirror_builtin_to_plugin()
    Called from post_tool_call hook.  Receives the built-in ``memory`` tool's
    arguments and result, then mirrors the write into the plugin's SQLite
    store (zone=core for MEMORY.md, zone=general for USER.md).

  Dir B (plugin → built-in): mirror_plugin_to_builtin()
    Called from _tool_srh_memory_write handler.  When the plugin writes a
    short, high-signal memory (zone=core, body≤200 chars), it mirrors the
    fact into MEMORY.md so the frozen system-prompt snapshot is updated on
    the next session start.

Safety guarantees:
  - No infinite loop: Dir B only triggers on zone=core — Dir A writes core
    entries, but Dir B only syncs from plugin → built-in, not the reverse.
  - Dedup check before every write.
  - Capacity check before every Dir B write.
  - Thread-safe via _mirror_lock.
  - All errors degrade silently — never block the caller.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_SECTION = "mem_reflection_hermes"
DIR_B_MAX_CHARS = 200          # Max body length for plugin → built-in sync
DIR_B_SYNC_ZONES = ("core",)   # Which zones trigger Dir B
ENTRY_DELIMITER = "\n§\n"      # Must match memory_tool.py

# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

_mirror_lock = threading.Lock()

_bridge_stats: Dict[str, int] = {
    "dir_a_mirror": 0,
    "dir_b_mirror": 0,
    "dir_a_skip_dup": 0,
    "dir_b_skip_dup": 0,
    "dir_b_skip_zone": 0,
    "dir_b_skip_long": 0,
    "dir_b_skip_fallback": 0,
    "errors": 0,
}
_bridge_stats_lock = threading.Lock()

# Test hook: inject a MemoryStore for testing on_memory_write
_test_mem_store: Optional[Any] = None


def _set_test_mem_store(store: Any) -> None:
    """Inject a MemoryStore for testing (bypasses relative imports)."""
    global _test_mem_store
    _test_mem_store = store


def _incr_stat(key: str) -> None:
    with _bridge_stats_lock:
        _bridge_stats[key] = _bridge_stats.get(key, 0) + 1


def get_bridge_stats() -> Dict[str, int]:
    with _bridge_stats_lock:
        return dict(_bridge_stats)


def reset_bridge_stats() -> None:
    with _bridge_stats_lock:
        for k in _bridge_stats:
            _bridge_stats[k] = 0


# ---------------------------------------------------------------------------
# Paths (resolved dynamically, matching plugin store semantics)
# ---------------------------------------------------------------------------

def _hermes_home() -> Path:
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        return Path(env_home)
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-untyped]
        return get_hermes_home()
    except Exception:
        return Path.home() / ".hermes"


_BUILTIN_MEMORY_DIR_CACHE: Optional[Path] = None
_BUILTIN_MEMORY_DIR_MTIME: float = 0.0


def _get_builtin_memory_dir() -> Path:
    """Resolve the built-in memory directory (profile-scoped)."""
    global _BUILTIN_MEMORY_DIR_CACHE, _BUILTIN_MEMORY_DIR_MTIME
    home = _hermes_home()
    d = home / "memories"
    # Simple cache: recalc only when home changes
    if _BUILTIN_MEMORY_DIR_CACHE is None or _BUILTIN_MEMORY_DIR_CACHE.parent != home:
        _BUILTIN_MEMORY_DIR_CACHE = d
    return d


def _builtin_path_for(target: str) -> Path:
    """MEMORY.md or USER.md path.

    Matches memory_tool.py convention: ``"memory"`` → ``MEMORY.md``,
    ``"user"`` → ``USER.md`` (all-caps, no .md extension on target).
    """
    fname = "MEMORY.md" if target.lower() == "memory" else "USER.md"
    return _get_builtin_memory_dir() / fname


# ---------------------------------------------------------------------------
# Built-in memory file operations (same locking convention as memory_tool.py)
# ---------------------------------------------------------------------------

def _acquire_file_lock(path: Path) -> Optional[Any]:
    """Acquire exclusive file lock using the same .lock convention as memory_tool.py.

    Returns a file object (caller must close) or None if locking is unavailable.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = open(lock_path, "a+", encoding="utf-8")
    except OSError:
        return None
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
    except ImportError:
        try:
            import msvcrt  # type: ignore[import-not-found]
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
        except ImportError:
            # No locking available — just pass
            pass
    except Exception:
        fd.close()
        return None
    return fd


def _release_file_lock(fd: Any) -> None:
    """Release the file lock."""
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except ImportError:
        try:
            import msvcrt  # type: ignore[import-not-found]
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
    try:
        fd.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Built-in memory reading
# ---------------------------------------------------------------------------

def _read_builtin_entries(target: str) -> List[str]:
    """Read entries from MEMORY.md or USER.md.

    Returns [] if file missing or unreadable.
    """
    path = _builtin_path_for(target)
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        return [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]
    except OSError:
        return []


def _is_duplicate_in_builtin(body: str, target: str = "memory") -> bool:
    """Check if an exact duplicate already exists in MEMORY.md/USER.md."""
    entries = _read_builtin_entries(target)
    return body.strip() in (e.strip() for e in entries)


def _char_count_builtin(target: str) -> int:
    """Return current total character count of built-in memory file."""
    entries = _read_builtin_entries(target)
    if not entries:
        return 0
    return len(ENTRY_DELIMITER.join(entries))


def _append_to_builtin(target: str, body: str) -> bool:
    """Append an entry to MEMORY.md/USER.md under file lock.

    Returns True on success.  Checks capacity (same 2200/1375 limits as
    memory_tool.py) and skips duplicates.
    """
    body = body.strip()
    if not body:
        return False

    path = _builtin_path_for(target)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = _acquire_file_lock(path)
    if fd is not None:
        try:
            return _do_append_builtin(path, target, body)
        finally:
            _release_file_lock(fd)
    else:
        # Lock unavailable — try without lock (last resort)
        return _do_append_builtin(path, target, body)


def _do_append_builtin(path: Path, target: str, body: str) -> bool:
    """Internal append after lock acquisition."""
    # Capacity: memory=2200, user=1375
    limits = {"memory": 2200, "user": 1375, "Memory": 2200, "User": 1375}
    char_limit = limits.get(target, 2200)

    entries = _read_builtin_entries(target)

    # Duplicate check
    if body in (e.strip() for e in entries):
        return False

    # Capacity check
    test_entries = entries + [body]
    new_total = len(ENTRY_DELIMITER.join(test_entries))
    current = _char_count_builtin(target)
    if new_total > char_limit:
        logger.debug(
            "Dir B skip (built-in capacity): %s at %d/%d chars",
            target, current, char_limit,
        )
        return False

    # Write
    entries.append(body)
    content = ENTRY_DELIMITER.join(entries)
    try:
        path.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Plugin-store helpers (Dir A)
# ---------------------------------------------------------------------------

def _is_duplicate_in_plugin(body: str, mem_store) -> bool:
    """Check if an exact-duplicate body exists in the plugin store."""
    if not hasattr(mem_store, "search"):
        return False
    try:
        results = mem_store.search(body, k=5, include_history=False)
        for r in results:
            if r.body.strip() == body.strip():
                return True
    except Exception:
        pass
    return False


def _find_plugin_entry_by_content(
    body: str, mem_store,
) -> Optional[Any]:
    """Find a plugin store entry whose body matches exactly.

    Returns the LoadedMemory or None.
    """
    if not hasattr(mem_store, "search"):
        return None
    try:
        results = mem_store.search(body, k=5, include_history=False)
        for r in results:
            if r.body.strip() == body.strip():
                return r
    except Exception:
        pass
    return None


def _find_plugin_entry_by_substring(
    substring: str, mem_store,
) -> Optional[Any]:
    """Find a plugin entry whose body *contains* the substring."""
    if not hasattr(mem_store, "search"):
        return None
    try:
        results = mem_store.search(substring, k=10, include_history=False)
        for r in results:
            if substring in r.body:
                return r
        # Fallback: scan core zone directly
        if hasattr(mem_store, "list_by_zone"):
            for r in mem_store.list_by_zone("core"):
                if substring in r.body:
                    return r
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Dir A: built-in → plugin
# ---------------------------------------------------------------------------

def mirror_builtin_to_plugin(
    action: str,
    target: str,
    content: str,
    old_text: str = "",
    entries_after: Optional[List[str]] = None,
    mem_store=None,
) -> Dict[str, Any]:
    """Mirror a built-in ``memory`` tool write into the plugin store.

    Parameters
    ----------
    action : str
        One of ``"add"``, ``"replace"``, ``"remove"``.
    target : str
        ``"memory"`` (MEMORY.md) or ``"user"`` (USER.md).
    content : str
        The content being added/replaced.  Empty for removes.
    old_text : str
        The substring used to identify the entry being replaced/removed.
    entries_after : list[str] | None
        Full entry list after the mutation (from result["entries"]).
    mem_store : MemoryStore | None
        Plugin MemoryStore instance.  If None, the operation is skipped.

    Returns
    -------
    dict with keys ``mirrored``, ``skipped``, ``errors``.
    """
    if mem_store is None:
        return {"mirrored": 0, "skipped": 0, "errors": ["no mem_store"]}

    with _mirror_lock:
        return _do_mirror_builtin_to_plugin(
            action, target, content, old_text, entries_after, mem_store,
        )


def _do_mirror_builtin_to_plugin(
    action: str,
    target: str,
    content: str,
    old_text: str,
    entries_after: Optional[List[str]],
    mem_store,
) -> Dict[str, Any]:
    """Internal Dir A implementation (under lock)."""
    # Determine plugin zone: memory→core, user→general
    plugin_zone = "core" if target == "memory" else "general"

    # Detect new entry from entries_after (for add) or use content directly
    new_entry = content.strip() if content else ""

    if not new_entry:
        # For remove actions or empty content, nothing to mirror
        return {"mirrored": 0, "skipped": 0, "errors": []}

    result: Dict[str, Any] = {"mirrored": 0, "skipped": 0, "errors": []}

    try:
        if action == "add":
            # Check duplicate in plugin store
            if _is_duplicate_in_plugin(new_entry, mem_store):
                _incr_stat("dir_a_skip_dup")
                result["skipped"] += 1
                result["reason"] = "duplicate"
                return result

            # Write to plugin store
            _write_to_plugin(mem_store, new_entry, plugin_zone)
            result["mirrored"] = 1
            _incr_stat("dir_a_mirror")

        elif action == "replace":
            # Find matching plugin entry by old_text, then replace
            old_entry_text = old_text.strip()
            if not old_entry_text:
                result["skipped"] += 1
                return result

            existing = _find_plugin_entry_by_substring(old_entry_text, mem_store)
            if existing is not None:
                # Supersede the old entry
                _write_to_plugin(
                    mem_store, new_entry, existing.frontmatter.zone,
                    supersedes=[existing.id()],
                )
                result["mirrored"] = 1
                _incr_stat("dir_a_mirror")
            else:
                # No matching entry in plugin — just add as new
                if not _is_duplicate_in_plugin(new_entry, mem_store):
                    _write_to_plugin(mem_store, new_entry, plugin_zone)
                    result["mirrored"] = 1
                    _incr_stat("dir_a_mirror")
                else:
                    result["skipped"] += 1

        elif action == "remove":
            # Find matching plugin entry and mark as superseded
            old_entry_text = old_text.strip()
            if not old_entry_text:
                result["skipped"] += 1
                return result

            existing = _find_plugin_entry_by_substring(old_entry_text, mem_store)
            if existing is not None:
                # Write a tombstone superseding entry
                _write_to_plugin(
                    mem_store,
                    f"[removed: {existing.body[:100]}]",
                    existing.frontmatter.zone,
                    supersedes=[existing.id()],
                )
                _incr_stat("dir_a_mirror")

    except Exception as e:
        logger.debug("Dir A mirror failed: %s", e)
        result["errors"].append(str(e))
        _incr_stat("errors")

    return result


def _write_to_plugin(
    mem_store,
    body: str,
    zone: str,
    supersedes: Optional[List[str]] = None,
) -> None:
    """Write an entry into the plugin store."""
    try:
        from .store import MemoryFrontmatter  # type: ignore[import-untyped]
    except ImportError:
        # Fallback when loaded standalone (test imports or direct use)
        # Use a minimal frontmatter construction
        try:
            from store import MemoryFrontmatter  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("Dir A: cannot import MemoryFrontmatter")
            return

    fm = MemoryFrontmatter.new(
        source="bridge",
        confidence="medium",
        tags=["bridge", "auto-sync"],
        zone=zone,
    )
    if supersedes:
        fm.supersedes = supersedes
        fm.supersedes_reason = "Auto-synced from built-in memory update"
    try:
        mem_store.put("user", fm, body)
    except ValueError:
        # Duplicate id (unlikely but guard)
        pass


# ---------------------------------------------------------------------------
# Dir B: plugin → built-in
# ---------------------------------------------------------------------------

def mirror_plugin_to_builtin(
    body: str,
    zone: str,
    source: str = "srh_memory_write",
) -> Dict[str, Any]:
    """Mirror a plugin memory write to MEMORY.md (built-in).

    Only syncs short (≤200 chars), high-signal writes from zone=core.
    USER.md content is not synced (user profile is curated manually).

    Parameters
    ----------
    body : str
        The memory body text.
    zone : str
        Plugin memory zone.
    source : str
        Tool or hook name that triggered the sync (for logging).

    Returns
    -------
    dict with keys ``mirrored``, ``target``, ``entry``.
    """
    with _mirror_lock:
        return _do_mirror_plugin_to_builtin(body, zone, source)


def _do_mirror_plugin_to_builtin(
    body: str, zone: str, source: str,
) -> Dict[str, Any]:
    """Internal Dir B implementation (under lock)."""
    body = body.strip()
    result: Dict[str, Any] = {"mirrored": False, "target": "", "entry": ""}

    # ── Eligibility ──────────────────────────────────────────────────
    if zone not in DIR_B_SYNC_ZONES:
        _incr_stat("dir_b_skip_zone")
        result["reason"] = f"zone '{zone}' not in sync list"
        return result

    if len(body) > DIR_B_MAX_CHARS:
        _incr_stat("dir_b_skip_long")
        result["reason"] = f"body too long ({len(body)} > {DIR_B_MAX_CHARS})"
        return result

    # ── Dedup ────────────────────────────────────────────────────────
    if _is_duplicate_in_builtin(body, target="memory"):
        _incr_stat("dir_b_skip_dup")
        result["reason"] = "duplicate in MEMORY.md"
        return result

    # ── Write ────────────────────────────────────────────────────────
    success = _append_to_builtin("memory", body)
    if success:
        _incr_stat("dir_b_mirror")
        result["mirrored"] = True
        result["target"] = "memory"
        result["entry"] = body
    else:
        _incr_stat("dir_b_skip_fallback")
        result["reason"] = "append failed (capacity or I/O)"

    return result


# ---------------------------------------------------------------------------
# Config query
# ---------------------------------------------------------------------------

def bridge_enabled() -> bool:
    """Check if the bridge is enabled in plugin config (default: True)."""
    try:
        from .store import plugin_config  # type: ignore[import-untyped]
        cfg = plugin_config()
    except ImportError:
        try:
            from store import plugin_config  # type: ignore[import-untyped]
            cfg = plugin_config()
        except ImportError:
            cfg = {}
    bridge_cfg = cfg.get("bridge", {})
    return bool(bridge_cfg.get("enabled", True))


# ---------------------------------------------------------------------------
# MemoryProvider adapter (on_memory_write bridge)
# ---------------------------------------------------------------------------

class MemoryBridgeProvider:
    """Thin MemoryProvider adapter that channels ``on_memory_write`` events.

    This is NOT a full MemoryProvider ABC subclass (avoids importing
    ``agent.memory_provider`` at module level).  It exposes the same
    interface so the plugin's ``register_memory_provider()`` can
    present it to the Hermes plugin system.

    The adapter:
    - Registers zero tools (tools are provided by the standalone plugin)
    - Receives ``on_memory_write`` callbacks from MemoryManager
    - Mirrors built-in memory writes to the plugin store
    """

    @property
    def name(self) -> str:
        return "mem-reflection-bridge"

    def is_available(self) -> bool:
        return bridge_enabled()

    def initialize(self, session_id: str, **kwargs) -> None:
        pass  # No initialization needed -- bridge reuses existing plugin singletons

    def get_tool_schemas(self):
        return []  # Tools are registered by the standalone plugin

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        return json.dumps({"error": f"Bridge provider does not handle tool '{tool_name}'"})

    def shutdown(self) -> None:
        pass

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror built-in memory writes to the plugin store.

        This is called by MemoryManager when the built-in ``memory`` tool
        writes an entry.  It mirrors the write into the plugin's SQLite
        store, mirroring the same logic as ``mirror_builtin_to_plugin()``
        but using the structured event data from MemoryManager.
        """
        if not bridge_enabled():
            return
        try:
            from .store import MemoryFrontmatter  # type: ignore[import-untyped]
        except ImportError:
            try:
                from store import MemoryFrontmatter  # type: ignore[import-untyped]
            except ImportError:
                return

        if not content or not content.strip():
            return

        zone = "core" if target == "memory" else "general"

        # Use injected test store if available (bypasses package imports)
        global _test_mem_store
        if _test_mem_store is not None:
            store = _test_mem_store
        else:
            try:
                # Get the plugin's MemStore singleton
                from . import _get_mem_store as _gms  # type: ignore[import-untyped]
                store = _gms()
            except Exception:
                try:
                    from .store import MemoryStore
                    from .store import user_memories_dir, plugin_data_dir
                    store = MemoryStore(user_memories_dir(), None,
                                        db_path=plugin_data_dir() / "memories.db")
                except Exception:
                    try:
                        # Direct import fallback (test / standalone context)
                        from store import MemoryStore, user_memories_dir, plugin_data_dir  # type: ignore[import-untyped]
                        store = MemoryStore(user_memories_dir(), None,
                                            db_path=plugin_data_dir() / "memories.db")
                    except Exception:
                        logger.debug("Bridge Provider: cannot get plugin store")
                        return

        # Dedup check before writing
        try:
            if hasattr(store, "search"):
                hits = store.search(content, k=5, include_history=False)
                if any(h.body.strip() == content.strip() for h in hits):
                    _incr_stat("dir_a_skip_dup")
                    return
        except Exception:
            pass

        fm = MemoryFrontmatter.new(
            source="bridge",
            confidence="medium",
            tags=["bridge", "auto-sync"],
            zone=zone,
        )
        try:
            store.put("user", fm, content.strip())
            _incr_stat("dir_a_mirror")
        except ValueError:
            # Duplicate (rare)
            pass
        except Exception as e:
            logger.debug("Bridge Provider put failed: %s", e)
            _incr_stat("errors")
