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
    from .memory_bridge import _refine_body
except ImportError:
    def _refine_body(body: str, max_chars: int = 500) -> str:
        return body.strip()[:max_chars]

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
    """Read curator config from plugin config, merging with defaults."""
    try:
        from .store import plugin_config
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
    return bool(cfg.get("enabled", True))


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
        from .store import plugin_data_dir
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
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return entries


def _append_to_cold_store(mem_store, entry: Dict[str, Any]) -> bool:
    """Append one entry to cold storage JSONL. Returns True on success."""
    path = _cold_store_path(mem_store)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _cold_store_lock:
            cap_mb = _curator_config(mem_store).get("cold_storage", {}).get("max_archive_size_mb", 10)
            # Prune oldest entries if over cap
            if path.exists() and path.stat().st_size >= cap_mb * 1024 * 1024:
                _prune_cold_store(mem_store, cap_mb)
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
    # Estimate size per entry and remove oldest
    entries.sort(key=lambda e: e.get("archived_at", ""))
    pruned = 0
    lo, hi = 0, len(entries)
    while lo < hi:
        mid = (lo + hi) // 2
        test_json = json.dumps(entries[mid:], ensure_ascii=False, default=str)
        if len(test_json.encode("utf-8")) <= cap_bytes:
            hi = mid
        else:
            lo = mid + 1
    if lo > 0:
        entries = entries[lo:]
        pruned = lo
    path = _cold_store_path(mem_store)
    try:
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
    return pruned


def _restore_from_cold(mem_store, memory_id: str) -> bool:
    """Restore a memory from cold storage back to active zone."""
    entries = _load_cold_store(mem_store)
    found = None
    for i, e in enumerate(entries):
        if e.get("id") == memory_id:
            found = entries.pop(i)
            break
    if found is None:
        return False
    # Re-write cold store minus the restored entry
    path = _cold_store_path(mem_store)

    # Restore to active store FIRST before touching archive
    try:
        from .store import MemoryFrontmatter
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
    except Exception as e:
        logger.warning("Cold restore failed to write active memory: %s", e)
        return False

    # Only rewrite cold store AFTER successful restore
    try:
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
    return True


# ── Phase 1: TTL + Staleness ──────────────────────────────────

def scan_for_stale(mem_store) -> List[str]:
    """Find memory IDs that are stale (expired or long-unaccessed).

    Returns list of memory IDs to archive/delete.
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

        # Check valid_until expiry
        valid_until = getattr(fm, "valid_until", None)
        if valid_until:
            try:
                expiry = datetime.fromisoformat(valid_until)
                if expiry < datetime.now(timezone.utc):
                    stale_ids.append(mid)
                    continue
            except (ValueError, TypeError):
                pass

        # Check access time (effectiveness gives us access recency)
        try:
            eff = _load_effectiveness(mem_store, mid)
            last_access = eff.get("last_accessed", 0) if eff else 0
        except Exception:
            last_access = 0

        if last_access > 0 and (now - last_access) > stale_days * 86400:
            stale_ids.append(mid)
            continue

        # Check effectiveness score
        if eff:
            score = eff.get("effectiveness", 0.5)
            if score < eff_threshold:
                stale_ids.append(mid)
                continue

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
    """Move expired/stale memories to cold storage. Returns count archived."""
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
        if _append_to_cold_store(mem_store, entry):
            try:
                mem_store.delete(mem.scope, mid)
                archived += 1
            except Exception:
                pass
    return archived


# ── Phase 2: Supersedes Archiving ─────────────────────────────

def archive_superseded(mem_store) -> int:
    """Archive deep supersedes chains (depth >= 2) with no recent access.

    Returns count of memories archived.
    """
    try:
        # Must include historical/superseded memories — list_active() filters them out
        if hasattr(mem_store, 'list'):
            all_active = mem_store.list(active_only=False)
        else:
            all_active = mem_store.list_active()
    except Exception:
        return 0
    now = time.time()
    archived = 0
    for mem in all_active:
        fm = mem.frontmatter
        mid = mem.id()
        # Only process memories that have been superseded
        if not fm.supersedes:
            continue
        # Check if the memory itself has been superseded by something newer
        # (supersedes list means this is an OLDER version, but we also need to
        # check if NEWER versions exist that supersede THIS memory)
        superseded_by = _find_superseding_memories(mem_store, mid)
        if not superseded_by:
            continue
        # Check if no recent access
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
            "zone": fm.zone,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "tags": list(fm.tags or []) + ["archived", "cold", "superseded"],
            "supersedes_chain": list(fm.supersedes),
            "superseded_by": superseded_by,
            "original_frontmatter": {
                "created": fm.created,
                "confidence": fm.confidence,
                "pinned": fm.pinned,
                "supersedes_reason": getattr(fm, "supersedes_reason", ""),
            },
        }
        if _append_to_cold_store(mem_store, entry):
            try:
                mem_store.delete(mem.scope, mid)
                archived += 1
            except Exception:
                pass
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


# ── Phase 3: Similarity Detection ──────────────────────────────

def scan_for_similar(mem_store) -> List[Tuple[str, str, float]]:
    """Find similar memory pairs using BM25 overlap.

    Returns list of (id_a, id_b, score) tuples above threshold.
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

    # Clamp to 500 most recent for performance
    all_active = sorted(all_active, key=lambda m: getattr(m, "_sort_key", ""), reverse=True)[:500]

    candidates: List[Tuple[str, str, float]] = []
    seen: set = set()
    try:
        from .store import _tokenise as _tokenise_fn
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


# ── Phase 4: Cold Storage (already implemented above via helpers) ─


# ── Phase 5: Report ────────────────────────────────────────────

def generate_report(
    stale_count: int,
    archived_count: int,
    superseded_count: int,
    similar_pairs: int,
    errors: List[str],
) -> str:
    """Generate a text summary of curator actions for the reflection log."""
    parts: List[str] = []
    if stale_count:
        parts.append(f"stale: {stale_count} archived")
    if archived_count:
        parts.append(f"expired: {archived_count} archived")
    if superseded_count:
        parts.append(f"superseded: {superseded_count} archived")
    if similar_pairs:
        parts.append(f"similar: {similar_pairs} candidate pair(s) found")
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
        "similar": 0,
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

    # Phase 3: Similarity Detection
    try:
        similar = scan_for_similar(mem_store)
        result["similar"] = len(similar)
    except Exception as e:
        errors.append(f"similar: {e}")
        logger.warning("Curator similarity scan failed: %s", e)

    result["errors"] = errors
    report_text = generate_report(
        stale_count=result["stale"],
        archived_count=result["archived"],
        superseded_count=result["superseded"],
        similar_pairs=result["similar"],
        errors=errors,
    )
    result["report"] = report_text
    # Persist the last run report for dashboard consumption
    try:
        report_path = _cold_store_path(mem_store).with_suffix(".report.json")
        report_path.write_text(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "report": report_text,
            "stale": result["stale"],
            "archived": result["archived"],
            "superseded": result["superseded"],
            "similar": result["similar"],
            "errors": result["errors"],
        }, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass
    return result
