"""memory_curator — Automated memory lifecycle management.

Runs after reflection + compaction in ``on_session_end``. Pure-rule driven;
no LLM calls. All curator actions logged to reflect-log.jsonl for audit.
Fail-open: exceptions are caught and logged, never block session lifecycle.

Curator Pipeline (in order):
  1. archive_superseded() — deep supersedes chains → cold storage
  2. scan_for_stale() — TTL + access-pattern based expiry
  3. scan_for_similar() — BM25 overlap similarity (flag candidates)
  4. cold_storage() — move dormant entries to JSONL archive
  5. generate_report() — text summary for reflection log
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .bridge import _refine_body
except ImportError:
    # Inline implementation to avoid dependency on memory_bridge
    def _refine_body(body: str, max_chars: int = 500) -> str:
        """Strip tool-call noise and truncate long memory bodies.

        Applied at cold-store archive time so stored memories
        contain clean, concise content rather than raw tool output.

        Strips:
          - Fenced code blocks with common language tags
          - [Tool: xxx] / [tool_output] markers
          - Excess whitespace and blank lines
        Then truncates at the nearest sentence boundary within max_chars.
        """
        text = body.strip()
        if not text:
            return text

        # Strip fenced code blocks
        text = re.sub(
            r"```(?:json|python|yaml|xml|bash|sql|text|markdown|toml|ini|sh|shell|console|diff|patch)?\s*\n.*?\n```",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Strip quad-backtick code blocks
        text = re.sub(
            r"````\w*\s*\n.*?\n````",
            "",
            text,
            flags=re.DOTALL,
        )

        # Strip [Tool: xxx] / [tool_output] markers
        text = re.sub(r"[\[{]\s*(?:[Tt][Oo][Oo][Ll]|tool|TOOL)\s*:?\s*\w+\s*[\]}]", "", text)
        text = re.sub(r"\[tool_output\].*?\[/tool_output\]", "", text, flags=re.DOTALL)
        text = re.sub(r"\{tool_output\}.*?\{/tool_output\}", "", text, flags=re.DOTALL)

        # Strip "Tool xxx result:" prefixes
        text = re.sub(r"Tool\s+\w+\s+(?:result|output|returned):\s*", "", text)

        # Collapse excess whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = text.strip()

        # Smart truncation at sentence boundary
        if len(text) > max_chars:
            truncated = text[:max_chars]
            last_period = truncated.rfind(".")
            last_question = truncated.rfind("?")
            last_break = max(last_period, last_question)
            last_space = truncated.rfind(" ")
            if last_break > max_chars * 0.7:
                text = text[: last_break + 1] + " .."
            elif last_space > max_chars * 0.5:
                text = text[:last_space] + " .."
            else:
                text = truncated + " .."

        return text

logger = logging.getLogger(__name__)

# ── Config keys ────────────────────────────────────────────────
_CURATOR_CFG_KEY = "curator"

# Thread-safe lock for cold store writes
_cold_store_lock = threading.Lock()

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


def _curator_config(mem_store) -> Dict[str, Any]:
    """Read curator config from plugin config, merging with defaults.

    Note: mem_store parameter is unused but kept for API consistency with
    other config helpers that may need store-specific config in the future.
    """
    try:
        from ..core.store import plugin_config
        cfg = plugin_config().get(_CURATOR_CFG_KEY, {})
    except Exception:
        cfg = {}
    merged = dict(_DEFAULT_CFG)
    merged.update(cfg)
    # Deep-merge sub-dicts
    for key in ("ttl", "stale", "episode", "similarity", "cold_storage"):
        if key in cfg and isinstance(cfg[key], dict):
            merged[key] = dict(merged.get(key, {}))
            merged[key].update(cfg[key])
    return merged


def _curator_enabled(mem_store) -> bool:
    """Is the curator active? Checks config + trigger condition."""
    cfg = _curator_config(mem_store)
    if not cfg.get("enabled", True):
        return False
    # Validate trigger
    trigger = cfg.get("trigger", "session_end")
    if trigger != "session_end":
        logger.warning(
            "Curator trigger '%s' is not supported. Only 'session_end' is currently implemented.",
            trigger,
        )
        # Still return True to not break existing config, but warn user
    return True


# ── Cold Storage Helpers ───────────────────────────────────────

_COLD_STORE_FILENAME = "cold_store.jsonl"


def _cold_store_path(mem_store) -> Path:
    """Path to the cold storage JSONL file."""
    # Allow caller to override path (used in tests)
    if hasattr(mem_store, '_cold_store_path_override'):
        p = Path(mem_store._cold_store_path_override)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    try:
        from ..core.store import plugin_data_dir
        return plugin_data_dir() / _COLD_STORE_FILENAME
    except Exception:
        return Path.home() / ".hermes" / "memory" / _COLD_STORE_FILENAME


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


def _append_to_cold_store(mem_store, entry: Dict[str, Any]) -> bool:
    """Append one entry to cold storage JSONL. Returns True on success.

    Note: The capacity check is performed outside the lock to avoid holding
    the lock during file I/O. This creates a small race window where multiple
    threads may see the file as under-capacity and both append, causing a
    brief over-limit state. The next append will trigger pruning. This is
    acceptable for the use case (archival is not latency-sensitive).
    """
    path = _cold_store_path(mem_store)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        cap_mb = _curator_config(mem_store).get("cold_storage", {}).get("max_archive_size_mb", 10)
        # Check capacity outside lock (see note above)
        if path.exists() and path.stat().st_size >= cap_mb * 1024 * 1024:
            with _cold_store_lock:
                _prune_cold_store(mem_store, cap_mb)
        with _cold_store_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return True
    except OSError as e:
        logger.warning("Cold storage append failed: %s", e)
        return False


def _prune_cold_store(mem_store, cap_mb: int) -> int:
    """Prune oldest entries from cold store when over cap. Returns count removed."""
    entries = _load_cold_store(mem_store)
    if not entries:
        return 0
    cap_bytes = cap_mb * 1024 * 1024

    # Pre-compute size of each entry for O(n) pruning
    # instead of O(n log n) binary search with repeated json.dumps
    entries_with_size = []
    for e in entries:
        try:
            serialized = json.dumps(e, ensure_ascii=False, default=str)
            size = len(serialized.encode("utf-8"))
            entries_with_size.append((e, size))
        except Exception:
            # Skip unserializable entries
            continue

    # Sort by archived_at (oldest first)
    entries_with_size.sort(key=lambda x: x[0].get("archived_at", ""))

    # Find the cutoff point by accumulating sizes
    total_size = sum(size for _, size in entries_with_size)
    if total_size <= cap_bytes:
        return 0  # No pruning needed

    # Remove oldest entries until we're under the cap
    pruned = 0
    remaining_entries = []
    current_size = total_size

    for entry, size in entries_with_size:
        if current_size <= cap_bytes:
            remaining_entries.append(entry)
        else:
            current_size -= size
            pruned += 1

    # Write the pruned store
    path = _cold_store_path(mem_store)
    try:
        with open(path, "w", encoding="utf-8") as f:
            for entry in remaining_entries:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        logger.warning("Cold store prune failed to write pruned file: %s — %s", path, e)
    return pruned


def _restore_from_cold(mem_store, memory_id: str) -> bool:
    """Restore a memory from cold storage back to active zone.

    Transaction semantics: restores to active store FIRST, then removes
    from cold store only after successful restore. If restore fails, cold
    store remains unchanged (safe rollback).
    """
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

    # Restore to active store FIRST (idempotent, safe to retry)
    try:
        try:
            from ..core.store import MemoryFrontmatter
        except ImportError:
            from core.store import MemoryFrontmatter  # fallback for direct imports
        zone = found.get("zone", "general")
        orig_fm = found.get("original_frontmatter", {})
        fm = MemoryFrontmatter(
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
        mem_store.put("user", fm, found.get("body", ""))
        # P2b: Graph ensure_meta after successful restore (fail-open)
        try:
            from ..runtime.graph import get_graph_manager_compat
            gm = get_graph_manager_compat()
            if gm is not None:
                gm.store.ensure_meta(fm.id, zone=zone)
        except Exception:
            pass
    except Exception as e:
        logger.warning("Cold restore failed to write active memory: %s", e)
        return False

    # Only rewrite cold store AFTER successful restore
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
        # Still return True because restore succeeded
    return True


# ── Phase 1: TTL + Staleness ──────────────────────────────────

def scan_for_stale(mem_store) -> List[str]:
    """Find memory IDs that are stale (expired or long-unaccessed).

    Returns list of memory IDs to archive/delete.

    Note: This function returns only IDs (not full memory objects) for API
    simplicity. The caller (archive_expired) will fetch each memory by ID.
    This adds one extra query per memory but keeps the API clean and allows
    the caller to decide what to do with each memory (archive, delete, etc.).
    """
    cfg = _curator_config(mem_store)
    stale_days = cfg.get("stale", {}).get("days", 90)
    eff_threshold = cfg.get("stale", {}).get("effectiveness_threshold", 0.1)
    now = time.time()
    stale_ids: List[str] = []

    try:
        all_active = mem_store.list_active()
    except Exception:
        return []

    for mem in all_active:
        fm = mem.frontmatter
        mid = mem.id()

        # Skip pinned or explicitly kept
        if fm.pinned:
            continue
        if fm.tags and any(t in ("keep", "permanent") for t in fm.tags):
            continue

        # Check if stale by any of the following criteria
        is_stale = False

        # Criterion 1: explicit valid_until expiry
        valid_until = getattr(fm, "valid_until", None)
        if valid_until:
            try:
                expiry = datetime.fromisoformat(valid_until)
                if expiry < datetime.now(timezone.utc):
                    is_stale = True
            except (ValueError, TypeError):
                pass

        # Criterion 2: long-unaccessed (stale days threshold)
        if not is_stale:
            eff = None
            try:
                eff = _load_effectiveness(mem_store, mid)
                last_access = eff.get("last_accessed", 0) if eff else 0
            except Exception:
                last_access = 0

            if last_access > 0 and (now - last_access) > stale_days * 86400:
                is_stale = True

            # Criterion 3: low effectiveness score
            elif eff:
                score = eff.get("effectiveness", 0.5)
                if score < eff_threshold:
                    is_stale = True

        if is_stale:
            stale_ids.append(mid)

    return stale_ids


def _load_effectiveness(mem_store, memory_id: str) -> Optional[Dict[str, Any]]:
    """Load effectiveness stats for a single memory."""
    try:
        eff_dict = mem_store.list_active_effectiveness()
        if isinstance(eff_dict, dict):
            return eff_dict.get(memory_id)
    except Exception:
        pass
    return None


def archive_expired(mem_store, memory_ids: List[str]) -> int:
    """Move expired/stale memories to cold storage. Returns count archived.

    Transaction semantics: if deletion fails after cold storage append,
    the entry remains in cold storage (safe) and a warning is logged.
    The memory will be re-archived on the next curator run.
    """
    archived = 0
    for mid in memory_ids:
        mem = mem_store.get(mid)
        if mem is None:
            continue
        entry = {
            "id": mid,
            "body": _refine_body(mem.body),
            "zone": mem.frontmatter.zone,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "tags": list(mem.frontmatter.tags or []) + ["archived", "cold"],
            "original_frontmatter": {
                "created": mem.frontmatter.created,
                "confidence": mem.frontmatter.confidence,
                "pinned": mem.frontmatter.pinned,
                "supersedes": list(mem.frontmatter.supersedes or []),
                "supersedes_reason": getattr(mem.frontmatter, "supersedes_reason", ""),
            },
        }
        # Append to cold storage first (safe backup)
        if _append_to_cold_store(mem_store, entry):
            # Try to delete from active store
            try:
                mem_store.delete(mem.scope, mid)
                archived += 1
            except Exception as e:
                # Deletion failed — log warning but keep cold storage entry
                logger.warning(
                    "Failed to delete memory %s after archiving: %s. "
                    "Cold storage entry preserved; memory may be re-archived on next run.",
                    mid, e,
                )
    return archived


# ── Phase 2: Supersedes Archiving ─────────────────────────────

def archive_superseded(mem_store) -> int:
    """Archive deep supersedes chains (depth >= 2) with no recent access.

    A chain has depth >= 2 when it has at least 3 nodes: A → B → C.
    The latest version (A, with no superseder) is always preserved.
    Older versions (B, C, ...) are archived if not accessed recently.

    Returns count of memories archived.
    """
    try:
        # Must include historical/superseded memories — list_active() filters them out
        if hasattr(mem_store, 'list'):
            all_items = mem_store.list(active_only=False)
        else:
            all_items = mem_store.list_active()
    except Exception:
        return 0
    now = time.time()
    archived = 0

    # Build a map: memory_id -> list of memories that supersede it
    superseded_by_map: Dict[str, List[str]] = {}
    for mem in all_items:
        fm = mem.frontmatter
        if fm.supersedes:
            for parent_id in fm.supersedes:
                if parent_id not in superseded_by_map:
                    superseded_by_map[parent_id] = []
                superseded_by_map[parent_id].append(mem.id())

    # Find all chain heads (memories with no superseder)
    chain_heads = [m for m in all_items if m.id() not in superseded_by_map]

    # For each chain head, traverse the chain and archive non-head nodes
    # if the chain depth >= 2 (at least 3 nodes)
    for head in chain_heads:
        fm = head.frontmatter
        head_id = head.id()

        # Skip pinned or explicitly kept
        if fm.pinned:
            continue
        if fm.tags and any(t in ("keep", "permanent") for t in fm.tags):
            continue

        # Traverse the chain from head backwards via supersedes links
        chain = [head_id]
        visited = {head_id}
        queue = [head_id]

        while queue:
            current_id = queue.pop(0)
            current_mem = mem_store.get(current_id)
            if current_mem is None:
                continue
            current_fm = current_mem.frontmatter
            if current_fm.supersedes:
                for parent_id in current_fm.supersedes:
                    if parent_id not in visited:
                        visited.add(parent_id)
                        chain.append(parent_id)
                        queue.append(parent_id)

        # Chain depth is the total number of nodes
        depth = len(chain)

        # Archive all non-head nodes if depth >= 3 (chain has at least 3 nodes)
        if depth >= 3:
            # Archive all nodes except the head (index 0)
            for i in range(1, depth):
                mid = chain[i]
                mem = mem_store.get(mid)
                if mem is None:
                    continue
                mem_fm = mem.frontmatter

                # Skip pinned or explicitly kept
                if mem_fm.pinned:
                    continue
                if mem_fm.tags and any(t in ("keep", "permanent") for t in mem_fm.tags):
                    continue

                # Check if no recent access (7 days)
                try:
                    eff = _load_effectiveness(mem_store, mid)
                    last_access = eff.get("last_accessed", 0) if eff else 0
                except Exception:
                    last_access = 0
                if last_access > 0 and (now - last_access) < 7 * 86400:
                    continue  # Recently accessed, keep

                entry = {
                    "id": mid,
                    "body": _refine_body(mem.body),
                    "zone": mem_fm.zone,
                    "archived_at": datetime.now(timezone.utc).isoformat(),
                    "tags": list(mem_fm.tags or []) + ["archived", "cold", "superseded"],
                    "supersedes_chain": list(mem_fm.supersedes or []),
                    "chain_depth": depth,
                    "chain_position": i,
                    "original_frontmatter": {
                        "created": mem_fm.created,
                        "confidence": mem_fm.confidence,
                        "pinned": mem_fm.pinned,
                        "supersedes_reason": getattr(mem_fm, "supersedes_reason", ""),
                    },
                }
                if _append_to_cold_store(mem_store, entry):
                    try:
                        mem_store.delete(mem.scope, mid)
                        archived += 1
                    except Exception as _de:
                        logger.warning(
                            "Failed to delete memory %s after archiving to cold store: %s",
                            mid, _de,
                        )

    return archived


def _find_superseding_memories(mem_store, memory_id: str) -> List[str]:
    """Find memories (including superseded) that supersede the given memory_id."""
    result: List[str] = []
    try:
        if hasattr(mem_store, 'list'):
            all_items = mem_store.list(active_only=False)
        else:
            all_items = mem_store.list_active()
        for m in all_items:
            if memory_id in (m.frontmatter.supersedes or []):
                result.append(m.id())
    except Exception:
        pass
    return result


# ── Phase 2b: Supersedes Chain Compaction (v1.3) ──────────


def compact_superseded_chains(mem_store) -> int:
    """Compress long supersedes chains by archiving intermediate nodes.

    For a chain v1→v2→v3→v4→v5 with length >= compact_min_chain:
      - v2, v3, v4 (intermediate) → cold storage with chain evidence
      - v5.supersedes updated to [v1] (skip intermediate)
      - Returns count of memories archived during compaction.
    """
    cfg = _curator_config(mem_store)
    min_chain = cfg.get("supersedes", {}).get("compact_min_chain", 3)
    protect_days = cfg.get("supersedes", {}).get("protect_days", 7)

    # Find chain heads: active memories that supersede something but are not superseded
    chain_heads: List[str] = []
    try:
        for mem in mem_store.list_active():
            if not mem.frontmatter.supersedes:
                continue
            if not getattr(mem_store, 'is_superseded', lambda _: False)(mem.id()):
                chain_heads.append(mem.id())
    except Exception:
        return 0

    now = time.time()
    archived = 0

    for head_id in chain_heads:
        try:
            # Walk backward from head to collect full chain
            chain: List[str] = []
            current_id = head_id
            visited: set = set()

            while current_id and current_id not in visited:
                visited.add(current_id)
                chain.insert(0, current_id)  # prepend → [oldest, ..., head]
                mem = mem_store.get(current_id)
                if mem is None or not mem.frontmatter.supersedes:
                    break
                current_id = mem.frontmatter.supersedes[0]  # immediate predecessor

            # chain[0] = oldest, chain[-1] = head (newest)
            if len(chain) < min_chain:
                continue

            # Check if any chain member was recently accessed (protect_days)
            recent = False
            for mid in chain:
                try:
                    eff = _load_effectiveness(mem_store, mid)
                    last_access = eff.get("last_accessed", 0) if eff else 0
                    if last_access > 0 and (now - last_access) < protect_days * 86400:
                        recent = True
                        break
                except Exception:
                    pass
            if recent:
                continue

            tail_id = chain[0]
            inter_ids = chain[1:-1]  # skip oldest and newest

            # Archive intermediate nodes
            for mid in inter_ids:
                mem = mem_store.get(mid)
                if mem is None:
                    continue
                entry = {
                    "id": mid,
                    "body": _refine_body(mem.body),
                    "zone": mem.frontmatter.zone,
                    "archived_at": datetime.now(timezone.utc).isoformat(),
                    "tags": list(mem.frontmatter.tags or []) + ["archived", "cold", "compacted"],
                    "supersedes_chain": list(chain),
                    "original_frontmatter": {
                        "created": mem.frontmatter.created,
                        "confidence": mem.frontmatter.confidence,
                        "pinned": mem.frontmatter.pinned,
                        "supersedes": list(mem.frontmatter.supersedes or []),
                    },
                }
                if _append_to_cold_store(mem_store, entry):
                    try:
                        mem_store.delete(mem.scope, mid)
                        archived += 1
                    except Exception:
                        pass

            # Update head's supersedes to skip intermediates
            if archived > 0:
                head = mem_store.get(head_id)
                if head is not None:
                    head.frontmatter.supersedes = [tail_id]

        except Exception:
            continue

    return archived


# ── Phase 3: Similarity Detection ──────────────────────────────

def scan_for_similar(mem_store) -> List[Tuple[str, str, float]]:
    """Find similar memory pairs using token overlap.

    Returns list of (id_a, id_b, score) tuples above threshold.

    NOTE: This uses simple Jaccard-like token overlap, not BM25 scoring.
    BM25 scoring can be implemented in Phase 2 for better accuracy.

    TODO: Implement LLM-based merge when curator.similarity.llm_merge is enabled.
    Currently this function only detects candidates; merging is manual.
    """
    cfg = _curator_config(mem_store)
    bm25_threshold = cfg.get("similarity", {}).get("bm25_threshold", 0.6)
    if not cfg.get("similarity", {}).get("enabled", True):
        return []

    try:
        all_active = mem_store.list_active()
    except Exception:
        return []

    if len(all_active) < 2:
        return []

    # Clamp to 500 for performance, but prioritize by last_accessed (more active)
    # rather than created date (which may miss old but frequently-used memories)
    try:
        # Sort by last_accessed if available, fall back to created
        def _sort_key(m):
            mem_id = m.id()
            try:
                eff = _load_effectiveness(mem_store, mem_id)
                if eff and "last_accessed" in eff:
                    return (0, -eff["last_accessed"])  # Sort by access time (neg for descending)
            except Exception:
                pass
            # Fallback to created timestamp
            return (1, getattr(m.frontmatter, 'created', ''))

        all_active = sorted(all_active, key=_sort_key)[:500]
    except Exception:
        # Fallback to original created-based sort if effectiveness loading fails
        all_active = sorted(all_active,
                            key=lambda m: getattr(getattr(m, 'frontmatter', None), 'created', ''),
                            reverse=True)[:500]

    candidates: List[Tuple[str, str, float]] = []
    seen: set = set()
    try:
        from ..core.store import _tokenise as _tokenise_fn
    except ImportError:
        import re as _re
        def _tokenise_fn(t):
            return _re.findall(r'\w+', t.lower())

    for i in range(len(all_active)):
        for j in range(i + 1, len(all_active)):
            key = tuple(sorted([all_active[i].id(), all_active[j].id()]))
            if key in seen:
                continue
            seen.add(key)
            try:
                tokens_a = set(_tokenise_fn(all_active[i].body))
                tokens_b = set(_tokenise_fn(all_active[j].body))
                if not tokens_a or not tokens_b:
                    continue
                overlap = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
                if overlap >= bm25_threshold:
                    candidates.append((all_active[i].id(), all_active[j].id(), round(overlap, 3)))
            except Exception:
                continue

    # Sort by score descending
    candidates.sort(key=lambda x: -x[2])
    return candidates


# ── Phase 3b: Similarity Merge ────────────────────────────


def merge_similar(mem_store) -> int:
    """Merge near-duplicate memories via supersedes chain.

    Uses scan_for_similar() candidates, then for each pair:
    - Exact duplicates: archive one, keep one
    - Near-duplicates: merge body+tags, supersedes-link the older one
    - Already-superseded memories are skipped

    Returns count of memories archived during merge.
    """
    cfg = _curator_config(mem_store)
    merge_threshold = cfg.get("similarity", {}).get("merge_threshold", 0.7)
    candidates = scan_for_similar(mem_store)
    if not candidates:
        return 0

    merged = 0
    for id_a, id_b, score in candidates:
        if score < merge_threshold:
            continue
        try:
            mem_a = mem_store.get(id_a)
            mem_b = mem_store.get(id_b)
            if mem_a is None or mem_b is None:
                continue

            # Skip if either memory has already been superseded
            fm_a = mem_a.frontmatter
            fm_b = mem_b.frontmatter
            if fm_a.supersedes or fm_b.supersedes:
                continue

            # Determine keeper (longer body) and archived (shorter)
            if len(mem_a.body) >= len(mem_b.body):
                keeper, archived = mem_a, mem_b
            else:
                keeper, archived = mem_b, mem_a

            # Exact dedup: bodies are identical
            if mem_a.body.strip() == mem_b.body.strip():
                # Archive the one with shorter id (deterministic)
                if id_a < id_b:
                    to_archive = mem_a
                else:
                    to_archive = mem_b
                # Archive directly without merge
                entry = {
                    "id": to_archive.id(),
                    "body": _refine_body(to_archive.body),
                    "zone": to_archive.frontmatter.zone,
                    "archived_at": datetime.now(timezone.utc).isoformat(),
                    "tags": list(to_archive.frontmatter.tags or []) + ["archived", "cold", "dedup"],
                    "original_frontmatter": {
                        "created": to_archive.frontmatter.created,
                        "confidence": to_archive.frontmatter.confidence,
                        "pinned": to_archive.frontmatter.pinned,
                        "supersedes": list(to_archive.frontmatter.supersedes or []),
                    },
                }
                if _append_to_cold_store(mem_store, entry):
                    try:
                        mem_store.delete(to_archive.scope, to_archive.id())
                        merged += 1
                    except Exception:
                        pass
                continue

            # Merge: combine bodies (dedup last-200 chars overlap)
            keeper_body = keeper.body.strip()
            archived_body = archived.body.strip()
            # Simple overlap dedup: if keeper ends with archived's start, skip
            overlap_len = 0
            for i in range(min(200, len(archived_body)), 0, -1):
                if keeper_body.endswith(archived_body[:i]):
                    overlap_len = i
                    break
            merged_body = keeper_body
            if overlap_len > 0:
                merged_body += "\n---\n" + archived_body[overlap_len:]
            else:
                merged_body += "\n---\n" + archived_body

            # Merge tags (union)
            merged_tags = list(set((keeper.frontmatter.tags or []) + (archived.frontmatter.tags or [])))

            # Update keeper
            mem_store.update(keeper.id(), body=merged_body, tags=merged_tags)

            # Archive the other via supersedes + cold storage
            entry = {
                "id": archived.id(),
                "body": _refine_body(archived.body),
                "zone": archived.frontmatter.zone,
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "tags": list(archived.frontmatter.tags or []) + ["archived", "cold", "merged"],
                "supersedes": [keeper.id()],  # Mark as superseded by keeper
                "original_frontmatter": {
                    "created": archived.frontmatter.created,
                    "confidence": archived.frontmatter.confidence,
                    "pinned": archived.frontmatter.pinned,
                    "supersedes": list(archived.frontmatter.supersedes or []),
                },
            }
            if _append_to_cold_store(mem_store, entry):
                try:
                    mem_store.delete(archived.scope, archived.id())
                    merged += 1
                except Exception:
                    pass
        except Exception:
            continue
    return merged


# ── Phase 4: Cold Storage (already implemented above via helpers) ─


# ── Phase 5: Orphan Edge Cleanup (P2a) ─────────────────────────

def clean_orphan_edges(mem_store) -> int:
    """Delete graph edges pointing to non-existent memories.

    Periodic sweep (Path B) — complements immediate cleanup via
    store.py post-delete callback.  Runs once per curator cycle.

    Returns number of orphan rows deleted (edges + graph_meta).
    Fail-open: if graph manager is unavailable or SQL fails, returns 0.
    """
    try:
        from ..runtime.graph import get_graph_manager_compat
        gm = get_graph_manager_compat()
    except Exception:
        gm = None
    if gm is None:
        return 0

    try:
        # Collect all currently valid memory IDs
        all_ids = {m.id() for m in mem_store.list_active()}
        if not all_ids:
            return 0
        return gm.store._gi.clean_orphan_edges(all_ids)
    except Exception as e:
        logger.warning("Curator orphan edge cleanup failed: %s", e)
        return 0


# ── Phase 6: Report ────────────────────────────────────────────

def generate_report(
    detected_stale: int,
    archived_stale: int,
    archived_superseded: int,
    similar_pairs: int,
    errors: List[str],
    merged_count: int = 0,
    compacted_count: int = 0,  # v1.3: chain compaction count
    orphan_count: int = 0,     # P2a: orphan edge cleanup count
) -> str:
    """Generate a text summary of curator actions for the reflection log."""
    parts: List[str] = []
    if detected_stale:
        parts.append(f"stale: {detected_stale} detected, {archived_stale} archived")
    elif archived_stale:
        parts.append(f"stale: {archived_stale} archived")
    if archived_superseded:
        parts.append(f"superseded: {archived_superseded} archived")
    if compacted_count:
        parts.append(f"compacted: {compacted_count} archived")
    if similar_pairs:
        parts.append(f"similar: {similar_pairs} candidate pair(s) found")
    if merged_count:
        parts.append(f"merged: {merged_count} archived")
    if orphan_count:
        parts.append(f"orphan edges: {orphan_count} cleaned")
    if errors:
        parts.append(f"errors: {len(errors)}")
    if not parts:
        return "No curator actions"
    return f"curator: {', '.join(parts)}"


# ── Main Entrypoint ────────────────────────────────────────────

def _run_curator(ctx, mem_store) -> Dict[str, Any]:
    """Run the full curator pipeline. Called from on_session_end.

    Returns dict with summary stats for the reflection log.
    Fail-open: all curation failures are caught and logged.
    """
    result: Dict[str, Any] = {
        "curator": True,
        "stale": 0,
        "archived": 0,
        "superseded": 0,
        "compacted": 0,  # v1.3: chain compaction
        "similar": 0,
        "merged": 0,  # v1.3: memories archived during merge
        "orphan_edges": 0,  # P2a: orphan edge cleanup
        "total_archived": 0,
        "errors": [],
    }
    errors: List[str] = []

    # Phase 1: TTL + Staleness
    try:
        stale_ids = scan_for_stale(mem_store)
        if stale_ids:
            archived = archive_expired(mem_store, stale_ids)
            result["stale"] = len(stale_ids)
            result["archived"] = archived
    except Exception as e:
        errors.append(f"stale: {e}")
        logger.warning("Curator stale scan failed: %s", e)

    # Phase 2: Supersedes Archiving
    try:
        superseded = archive_superseded(mem_store)
        result["superseded"] = superseded
    except Exception as e:
        errors.append(f"superseded: {e}")
        logger.warning("Curator supersedes archiving failed: %s", e)

    # Phase 2b: Chain Compaction (v1.3)
    try:
        compacted = compact_superseded_chains(mem_store)
        result["compacted"] = compacted
    except Exception as e:
        errors.append(f"compacted: {e}")
        logger.warning("Curator chain compaction failed: %s", e)

    # Recalculate total archived
    result["total_archived"] = result["archived"] + result["superseded"] + result["compacted"]

    # Phase 3: Similarity Detection
    try:
        similar = scan_for_similar(mem_store)
        result["similar"] = len(similar)
    except Exception as e:
        errors.append(f"similar: {e}")
        logger.warning("Curator similarity scan failed: %s", e)

    # Phase 3b: Similarity Merge (v1.3)
    try:
        merged = merge_similar(mem_store)
        result["merged"] = merged
    except Exception as e:
        errors.append(f"merge: {e}")
        logger.warning("Curator similarity merge failed: %s", e)

    # Recalculate total archived including merge
    result["total_archived"] = result["archived"] + result["superseded"] + result["merged"]

    # Phase 5: Orphan Edge Cleanup (P2a)
    try:
        orphan_cleaned = clean_orphan_edges(mem_store)
        result["orphan_edges"] = orphan_cleaned
    except Exception as e:
        errors.append(f"orphan_edges: {e}")
        logger.warning("Curator orphan edge cleanup failed: %s", e)

    result["errors"] = errors
    report_text = generate_report(
        detected_stale=result["stale"],
        archived_stale=result["archived"],
        archived_superseded=result["superseded"],
        similar_pairs=result["similar"],
        merged_count=result["merged"],
        compacted_count=result["compacted"],
        orphan_count=result["orphan_edges"],
        errors=errors,
    )
    result["report"] = report_text
    # Persist the last run report for dashboard consumption
    try:
        cold_path = _cold_store_path(mem_store)
        # Use fixed report filename in the same directory as cold store
        report_path = cold_path.parent / "curator-report.json"
        report_path.write_text(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "report": report_text,
            "stale": result["stale"],
            "archived": result["archived"],
            "superseded": result["superseded"],
            "compacted": result["compacted"],
            "similar": result["similar"],
            "merged": result["merged"],
            "orphan_edges": result["orphan_edges"],
            "total_archived": result["total_archived"],
            "errors": result["errors"],
        }, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write curator report file: %s", e)

    # Also append to reflect-log.jsonl for unified audit trail (ALIGN-1)
    try:
        from ..reflection.runtime import _append_reflect_log
        _append_reflect_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "curator",
            "summary": report_text,
            "stale": result["stale"],
            "archived": result["archived"],
            "superseded": result["superseded"],
            "compacted": result["compacted"],
            "similar": result["similar"],
            "merged": result["merged"],
            "orphan_edges": result["orphan_edges"],
            "errors": result["errors"],
        })
    except Exception:
        pass

    return result
