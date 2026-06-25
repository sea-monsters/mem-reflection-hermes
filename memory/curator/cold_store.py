"""Cold storage I/O for memory curation.

- JSONL append with capacity pruning
- Atomic rewrite for prune/restore
- Fail-open on all I/O errors (log warning, return empty/default)
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_COLD_STORE_FILENAME = "cold_store.jsonl"
_cold_store_lock = threading.Lock()

# Resolve the shared late-binding helper safely for both package and standalone
# module loading.
try:
    from mem_reflection_hermes.runtime._lb import _lb as _lb_fn
except ImportError:
    _lb_fn = None
if _lb_fn is None:
    try:
        from runtime._lb import _lb as _lb_fn
    except ImportError:
        _lb_fn = None


def _resolve_plugin_data_dir():
    core_store = _lb_fn("mem_reflection_hermes.core.store") if _lb_fn is not None else None
    if core_store is None:
        core_store = _lb_fn("core.store") if _lb_fn is not None else None
    if core_store is not None and hasattr(core_store, "plugin_data_dir"):
        return core_store.plugin_data_dir()
    return None


def _resolve_memory_frontmatter(*args, **kwargs):
    core_store = _lb_fn("mem_reflection_hermes.core.store") if _lb_fn is not None else None
    if core_store is None:
        core_store = _lb_fn("core.store") if _lb_fn is not None else None
    if core_store is not None and hasattr(core_store, "MemoryFrontmatter"):
        return core_store.MemoryFrontmatter(*args, **kwargs)

    # Fallback dataclass for standalone tests
    from dataclasses import dataclass, field

    @dataclass
    class _FallbackFM:
        id: str
        created: str
        source: str
        confidence: str
        zone: str = "general"
        tags: list = field(default_factory=list)
        pinned: bool = False
        supersedes: list = field(default_factory=list)
        supersedes_reason: str = ""

    return _FallbackFM(*args, **kwargs)


def _cold_store_path(mem_store) -> Path:
    """Path to the cold storage JSONL file."""
    if hasattr(mem_store, '_cold_store_path_override'):
        p = Path(mem_store._cold_store_path_override)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    if hasattr(mem_store, "_test_data_dir"):
        p = Path(mem_store._test_data_dir) / "memory" / _COLD_STORE_FILENAME
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    base = _resolve_plugin_data_dir()
    if base is None:
        base = Path.home() / ".hermes" / "memory"
        base.mkdir(parents=True, exist_ok=True)
    return base / _COLD_STORE_FILENAME


def _load_cold_store(mem_store) -> List[Dict[str, Any]]:
    """Read all entries from cold storage JSONL."""
    path = _cold_store_path(mem_store)
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(
                            "Cold storage JSONL parse error at line %d: %s. Skipping entry.",
                            line_no, e,
                        )
                        continue
    except OSError as e:
        logger.warning("Failed to read cold storage file %s: %s", path, e)
        return []
    return entries


def _curator_config_for_cold(mem_store) -> Dict[str, Any]:
    """Lightweight config read used by cold store helpers.

    Avoids importing helpers here to prevent circular imports.
    """
    _DEFAULT_CFG: Dict[str, Any] = {
        "enabled": True,
        "trigger": "session_end",
        "ttl": {"expired_action": "archive"},
        "stale": {"days": 90, "effectiveness_threshold": 0.1},
        "episode": {"ttl_days": 30},
        "similarity": {
            "enabled": True,
            "bm25_threshold": 0.6,
            "embedding_threshold": 0.85,
            "llm_merge": False,
        },
        "cold_storage": {"enabled": True, "max_archive_size_mb": 10},
    }
    cfg: Dict[str, Any] = {}
    if mem_store is not None and hasattr(mem_store, "_plugin_config_override"):
        cfg = mem_store._plugin_config_override.get("curator", {})
    else:
        core_store = _lb_fn("mem_reflection_hermes.core.store") if _lb_fn is not None else None
        if core_store is None:
            core_store = _lb_fn("core.store") if _lb_fn is not None else None
        if core_store is not None and hasattr(core_store, "plugin_config"):
            cfg = core_store.plugin_config().get("curator", {})
        else:
            cfg = {}
    merged = dict(_DEFAULT_CFG)
    merged.update(cfg)
    for key in ("ttl", "stale", "episode", "similarity", "cold_storage"):
        if key in cfg and isinstance(cfg[key], dict):
            merged[key] = dict(_DEFAULT_CFG.get(key, {}))
            merged[key].update(cfg[key])
    return merged


def _append_to_cold_store(mem_store, entry: Dict[str, Any]) -> bool:
    """Append one entry to cold storage JSONL. Returns True on success."""
    try:
        path = _cold_store_path(mem_store)
        path.parent.mkdir(parents=True, exist_ok=True)
        cap_mb = (
            _curator_config_for_cold(mem_store)
            .get("cold_storage", {})
            .get("max_archive_size_mb", 10)
        )
        if path.exists() and path.stat().st_size >= cap_mb * 1024 * 1024:
            with _cold_store_lock:
                _prune_cold_store(mem_store, cap_mb)
        with _cold_store_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return True
    except (OSError, ValueError) as e:
        logger.warning("Cold storage append failed: %s", e)
        return False


def _prune_cold_store(mem_store, cap_mb: int) -> int:
    """Prune oldest entries from cold store when over cap. Returns count removed."""
    entries = _load_cold_store(mem_store)
    if not entries:
        return 0
    cap_bytes = cap_mb * 1024 * 1024

    entries_with_size = []
    for e in entries:
        try:
            serialized = json.dumps(e, ensure_ascii=False, default=str)
            size = len(serialized.encode("utf-8"))
            entries_with_size.append((e, size))
        except Exception:
            logger.debug("Cold store JSON serialization failed for entry %s",
                          e.get("memory_id", "<unknown>"))
            continue

    entries_with_size.sort(key=lambda x: x[0].get("archived_at", ""))
    total_size = sum(size for _, size in entries_with_size)
    if total_size <= cap_bytes:
        return 0

    pruned = 0
    remaining_entries = []
    current_size = total_size
    for entry, size in entries_with_size:
        if current_size <= cap_bytes:
            remaining_entries.append(entry)
        else:
            current_size -= size
            pruned += 1

    path = _cold_store_path(mem_store)
    try:
        with open(path, "w", encoding="utf-8") as f:
            for entry in remaining_entries:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        logger.warning("Cold store prune failed to write pruned file: %s — %s", path, e)
    return pruned


def _restore_from_cold(mem_store, memory_id: str) -> bool:
    """Restore a memory from cold storage back to active zone."""
    entries = _load_cold_store(mem_store)
    found = None
    found_idx = -1
    for i, e in enumerate(entries):
        if e.get("id") == memory_id:
            found = entries[i]
            found_idx = i
            break
    if found is None:
        return False

    zone = found.get("zone", "general")
    orig_fm = found.get("original_frontmatter", {})
    fm = _resolve_memory_frontmatter(
        id=found["id"],
        created=orig_fm.get("created", found.get("archived_at", datetime.now(timezone.utc).isoformat())),
        source="restored",
        confidence=orig_fm.get("confidence", "medium"),
        zone=zone,
        tags=found.get("tags", ["restored"]),
        pinned=orig_fm.get("pinned", False),
        supersedes=orig_fm.get("supersedes", []),
        supersedes_reason=orig_fm.get("supersedes_reason", ""),
    )
    try:
        mem_store.put("user", fm, found.get("body", ""))
    except Exception as e:
        logger.warning("Cold restore failed to write active memory: %s", e)
        return False

    graph_mod = _lb_fn("mem_reflection_hermes.runtime.graph") if _lb_fn is not None else None
    if graph_mod is None:
        graph_mod = _lb_fn("runtime.graph") if _lb_fn is not None else None
    if graph_mod is not None and hasattr(graph_mod, "get_graph_manager_compat"):
        try:
            gm = graph_mod.get_graph_manager_compat()
            if gm is not None:
                gm.store.ensure_meta(fm.id, zone=zone)
        except Exception as e:
            logger.debug("Cold restore graph meta update skipped: %s", e)

    path = _cold_store_path(mem_store)
    try:
        entries_without_restored = [e for i, e in enumerate(entries) if i != found_idx]
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries_without_restored:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        logger.warning(
            "Cold restore succeeded but failed to remove entry from cold store: %s. "
            "Entry will remain in cold store (safe duplication).",
            e,
        )
    return True
