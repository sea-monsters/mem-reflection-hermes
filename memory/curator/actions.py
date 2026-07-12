"""Curator actions — composable pipeline phases.

Each phase is a CuratorAction subclass implementing execute(ctx) -> CuratorResult.
Shared logic lives in helpers.py; cold store I/O lives in cold_store.py.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

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

# Intra-curator imports: use normal relative imports in package mode; provide
# minimal standalone fallbacks so the module can still be loaded directly via
# importlib for tests and scripts.
try:
    from .helpers import (
        CuratorContext,
        CuratorResult,
        _curator_config,
        archive_and_delete,
        build_cold_entry,
        is_protected,
        load_last_access,
    )
except ImportError:
    _helpers_mod = _lb_fn("mem_reflection_hermes.memory.curator.helpers") if _lb_fn is not None else None
    if _helpers_mod is not None:
        CuratorContext = _helpers_mod.CuratorContext
        CuratorResult = _helpers_mod.CuratorResult
        _curator_config = _helpers_mod._curator_config
        archive_and_delete = _helpers_mod.archive_and_delete
        build_cold_entry = _helpers_mod.build_cold_entry
        is_protected = _helpers_mod.is_protected
        load_last_access = _helpers_mod.load_last_access
    else:
        from dataclasses import dataclass, field

        @dataclass
        class CuratorContext:  # type: ignore[no-redef]
            mem_store: Any
            filters: Optional[Dict[str, Optional[str]]] = None
            admin_global: bool = False
            scope_label: str = "local_global"
            errors: List[str] = field(default_factory=list)

            def list_active(self):
                if self.admin_global or not self.filters:
                    return self.mem_store.list_active()
                return self.mem_store.list_active(filters=self.filters)

        @dataclass
        class CuratorResult:  # type: ignore[no-redef]
            action_name: str
            archived: int = 0
            compacted: int = 0
            merged: int = 0
            similar_pairs: int = 0
            orphan_edges: int = 0
            typed_facts_deleted: int = 0
            journal_entries: List[Dict[str, Any]] = field(default_factory=list)
            errors: List[str] = field(default_factory=list)

        def _curator_config(mem_store):  # type: ignore[no-redef]
            return {
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
                "gc": {"typed_fact_retention_days": 30},
                "stop_on_error": False,
            }

        # Assign as lambdas so AST scans see exactly one FunctionDef per name.
        is_protected = (  # type: ignore[no-redef]
            lambda fm: getattr(fm, "pinned", False)
            or bool(getattr(fm, "tags", None)
                    and any(t in ("keep", "permanent") for t in getattr(fm, "tags", [])))
        )
        load_last_access = lambda mem_store, mid: 0.0  # type: ignore[no-redef]
        build_cold_entry = (  # type: ignore[no-redef]
            lambda mem, context_tag, **extra: {"id": mem.id(), "context_tag": context_tag, **extra}
        )
        archive_and_delete = lambda mem_store, mem, entry, context: (False, "standalone fallback")  # type: ignore[no-redef]


class CuratorAction:
    """Base class for a single curation phase."""

    name: str = ""

    def should_run(self, ctx: CuratorContext) -> bool:
        return True

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        raise NotImplementedError


class ArchiveStale(CuratorAction):
    """Phase 1: archive memories that are expired or long-unaccessed."""

    name = "ArchiveStale"

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        mem_store = ctx.mem_store
        cfg = _curator_config(mem_store)
        stale_days = cfg.get("stale", {}).get("days", 90)
        eff_threshold = cfg.get("stale", {}).get("effectiveness_threshold", 0.1)
        now = time.time()
        result = CuratorResult(action_name=self.name)

        try:
            all_active = ctx.list_active()
        except Exception as e:
            result.errors.append(f"list_active: {e}")
            logger.warning("Curator ArchiveStale failed to list active memories: %s", e)
            return result

        stale_ids: List[str] = []
        for mem in all_active:
            fm = mem.frontmatter
            mid = mem.id()
            if is_protected(fm):
                continue

            is_stale = False
            valid_until = getattr(fm, "valid_until", None)
            if valid_until:
                try:
                    from datetime import datetime, timezone
                    expiry = datetime.fromisoformat(valid_until)
                    if expiry < datetime.now(timezone.utc):
                        is_stale = True
                except (ValueError, TypeError):
                    pass

            if not is_stale:
                last_access = load_last_access(mem_store, mid)
                if last_access > 0 and (now - last_access) > stale_days * 86400:
                    is_stale = True
                else:
                    # P2-14: memories that pre-date the stats pipeline (or were
                    # created without recorded stats) would otherwise become
                    # immortal. Fall back to frontmatter.created or the file mtime
                    # when there is no last_access signal.
                    age_ts = 0.0
                    created = getattr(fm, "created", None)
                    if created:
                        try:
                            from datetime import datetime, timezone
                            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            age_ts = dt.timestamp()
                        except (ValueError, TypeError):
                            pass
                    if age_ts == 0 and getattr(mem, "source_path", None):
                        try:
                            age_ts = mem.source_path.stat().st_mtime
                        except OSError:
                            pass
                    if age_ts > 0 and (now - age_ts) > stale_days * 86400:
                        is_stale = True
                    else:
                        eff = None
                        try:
                            from .helpers import _load_effectiveness
                            eff = _load_effectiveness(mem_store, mid)
                        except Exception as e:
                            logger.debug("ArchiveStale: could not load effectiveness for %s: %s", mid, e)
                        if eff:
                            # Combined effectiveness score: hit-rate (factor, 0.5-1.0)
                            # weighted by time-decay (decay_factor, 0.3-1.0). Range
                            # ~0.15-1.0. A memory is stale when it is both rarely
                            # referenced AND long untouched.
                            score = eff.factor() * eff.decay_factor()
                            if score < eff_threshold:
                                is_stale = True

            if is_stale:
                stale_ids.append(mid)

        for mid in stale_ids:
            mem = mem_store.get(mid)
            if mem is None:
                continue
            entry = build_cold_entry(mem, context_tag="stale")
            success, err = archive_and_delete(mem_store, mem, entry, "stale")
            if success:
                result.archived += 1
                result.journal_entries.append({
                    "action": "archive",
                    "memory_id": mid,
                    "context_tag": "stale",
                    "scope_label": getattr(ctx, "scope_label", "local_global"),
                })
            elif err:
                result.errors.append(f"archive {mid}: {err}")

        return result


class CompactChains(CuratorAction):
    """Phase 2: compress long supersedes chains by archiving intermediates."""

    name = "CompactChains"

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        mem_store = ctx.mem_store
        cfg = _curator_config(mem_store)
        min_chain = cfg.get("supersedes", {}).get("compact_min_chain", 3)
        protect_days = cfg.get("supersedes", {}).get("protect_days", 7)
        now = time.time()
        result = CuratorResult(action_name=self.name)

        chain_heads: List[str] = []
        try:
            for mem in ctx.list_active():
                if not mem.frontmatter.supersedes:
                    continue
                if not getattr(mem_store, "is_superseded", lambda _: False)(mem.id()):
                    chain_heads.append(mem.id())
        except Exception as e:
            result.errors.append(f"list_active: {e}")
            logger.warning("Curator CompactChains failed to list active memories: %s", e)
            return result

        for head_id in chain_heads:
            try:
                chain: List[str] = []
                current_id = head_id
                visited: Set[str] = set()

                while current_id and current_id not in visited:
                    visited.add(current_id)
                    chain.insert(0, current_id)
                    mem = mem_store.get(current_id)
                    if mem is None or not mem.frontmatter.supersedes:
                        break
                    current_id = mem.frontmatter.supersedes[0]

                if len(chain) < min_chain:
                    continue

                recent = False
                for mid in chain:
                    last_access = load_last_access(mem_store, mid)
                    if last_access > 0 and (now - last_access) < protect_days * 86400:
                        recent = True
                        break
                if recent:
                    continue

                tail_id = chain[0]
                inter_ids = chain[1:-1]

                for mid in inter_ids:
                    mem = mem_store.get(mid)
                    if mem is None:
                        continue
                    mem_fm = mem.frontmatter
                    if is_protected(mem_fm):
                        continue
                    entry = build_cold_entry(
                        mem,
                        context_tag="compacted",
                        supersedes_chain=list(chain),
                        original_frontmatter={
                            "created": mem_fm.created,
                            "confidence": mem_fm.confidence,
                            "pinned": mem_fm.pinned,
                            "supersedes": list(mem_fm.supersedes or []),
                        },
                    )
                    success, err = archive_and_delete(mem_store, mem, entry, "compacted")
                    if success:
                        result.compacted += 1
                        result.journal_entries.append({
                            "action": "compact",
                            "memory_id": mid,
                            "context_tag": "compacted",
                            "chain": list(chain),
                            "scope_label": getattr(ctx, "scope_label", "local_global"),
                        })
                    elif err:
                        result.errors.append(f"compact {mid}: {err}")

                if result.compacted > 0:
                    try:
                        mem_store.update(head_id, supersedes=[tail_id])
                    except Exception as e:
                        logger.warning(
                            "Failed to update head %s supersedes after compaction: %s",
                            head_id, e,
                        )
                        result.errors.append(f"update head {head_id}: {e}")

            except Exception as e:
                result.errors.append(f"chain {head_id}: {e}")
                logger.warning("Curator CompactChains failed on chain %s: %s", head_id, e)
                continue

        return result


class ArchiveSuperseded(CuratorAction):
    """Phase 2a: archive deep supersedes chains (depth >= 3) after compaction."""

    name = "ArchiveSuperseded"

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        mem_store = ctx.mem_store
        result = CuratorResult(action_name=self.name)
        now = time.time()

        try:
            if hasattr(mem_store, "list"):
                all_items = mem_store.list(
                    active_only=False,
                    filters=None if getattr(ctx, "admin_global", False) else getattr(ctx, "filters", None),
                )
            else:
                all_items = ctx.list_active()
        except Exception as e:
            result.errors.append(f"list: {e}")
            logger.warning("Curator ArchiveSuperseded failed to list memories: %s", e)
            return result

        superseded_by_map: Dict[str, List[str]] = {}
        for mem in all_items:
            fm = mem.frontmatter
            if fm.supersedes:
                for parent_id in fm.supersedes:
                    superseded_by_map.setdefault(parent_id, []).append(mem.id())

        chain_heads = [m for m in all_items if m.id() not in superseded_by_map]

        for head in chain_heads:
            fm = head.frontmatter
            head_id = head.id()
            if is_protected(fm):
                continue

            chain = [head_id]
            visited: Set[str] = {head_id}
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

            if len(chain) < 3:
                continue

            for i in range(1, len(chain)):
                mid = chain[i]
                mem = mem_store.get(mid)
                if mem is None:
                    continue
                mem_fm = mem.frontmatter
                if is_protected(mem_fm):
                    continue

                last_access = load_last_access(mem_store, mid)
                if last_access > 0 and (now - last_access) < 7 * 86400:
                    continue

                entry = build_cold_entry(
                    mem,
                    context_tag="superseded",
                    supersedes_chain=list(mem_fm.supersedes or []),
                    chain_depth=len(chain),
                    chain_position=i,
                    original_frontmatter={
                        "created": mem_fm.created,
                        "confidence": mem_fm.confidence,
                        "pinned": mem_fm.pinned,
                        "supersedes_reason": getattr(mem_fm, "supersedes_reason", ""),
                    },
                )
                success, err = archive_and_delete(mem_store, mem, entry, "superseded")
                if success:
                    result.archived += 1
                    result.journal_entries.append({
                        "action": "archive",
                        "memory_id": mid,
                        "context_tag": "superseded",
                        "chain_depth": len(chain),
                        "scope_label": getattr(ctx, "scope_label", "local_global"),
                    })
                elif err:
                    result.errors.append(f"archive {mid}: {err}")

        return result


class MergeSimilar(CuratorAction):
    """Phase 3 + 3b: detect and merge near-duplicate memories."""

    name = "MergeSimilar"

    def _tokenise_fn(self, mem_store):
        core_store = _lb_fn("mem_reflection_hermes.core.store") if _lb_fn is not None else None
        if core_store is None:
            core_store = _lb_fn("core.store") if _lb_fn is not None else None
        if core_store is not None and hasattr(core_store, "_tokenise"):
            return core_store._tokenise

        def _fallback(t: str) -> List[str]:
            return re.findall(r"\w+", t.lower())

        return _fallback

    def _scan_for_similar(self, mem_store, ctx: Optional[CuratorContext] = None) -> List[Tuple[str, str, float]]:
        cfg = _curator_config(mem_store)
        bm25_threshold = cfg.get("similarity", {}).get("bm25_threshold", 0.6)
        if not cfg.get("similarity", {}).get("enabled", True):
            return []

        try:
            all_active = ctx.list_active() if ctx is not None else mem_store.list_active()
        except Exception:
            logger.warning("MergeSimilar: list_active failed", exc_info=True)
            return []

        if len(all_active) < 2:
            return []

        # v1.6: Group by scope to avoid merging memories from different scopes.
        def _scope_key(m):
            fm = m.frontmatter
            return (
                getattr(fm, "user_id", None),
                getattr(fm, "agent_id", None),
                getattr(fm, "run_id", None),
            )

        def _sort_key(m):
            mem_id = m.id()
            try:
                from .helpers import _load_effectiveness
                eff = _load_effectiveness(mem_store, mem_id)
                if eff and getattr(eff, "last_event_at", None):
                    # Sort by most-recently-active first (newer ISO sorts higher,
                    # so negate via reverse epoch of the last event).
                    from datetime import datetime, timezone
                    last = datetime.fromisoformat(eff.last_event_at.replace("Z", "+00:00"))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    return (0, -last.timestamp())
            except Exception as e:
                logger.debug("MergeSimilar sort key failed for %s: %s", mem_id, e)
            return (1, getattr(m.frontmatter, "created", ""))

        # Apply the advertised 500-memory cap BEFORE building scope groups.
        # The original code built groups from the full list and then sliced,
        # making the cap dead code and the scan O(n²) over the entire store.
        try:
            all_active = sorted(all_active, key=_sort_key)[:500]
        except Exception:
            logger.warning("MergeSimilar sort failed, using fallback", exc_info=True)
            all_active = sorted(
                all_active,
                key=lambda m: getattr(getattr(m, "frontmatter", None), "created", ""),
                reverse=True,
            )[:500]

        if len(all_active) > 400:
            logger.debug("MergeSimilar capped scan to %d most-recent memories", len(all_active))

        scope_groups: Dict[Tuple, List] = {}
        for m in all_active:
            scope_groups.setdefault(_scope_key(m), []).append(m)

        tokenise = self._tokenise_fn(mem_store)
        candidates: List[Tuple[str, str, float]] = []
        seen: Set[Tuple[str, str]] = set()

        # v1.6: Scan within scope groups only, not across scopes.
        for group in scope_groups.values():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    key = tuple(sorted([group[i].id(), group[j].id()]))
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        tokens_a = set(tokenise(group[i].body))
                        tokens_b = set(tokenise(group[j].body))
                        if not tokens_a or not tokens_b:
                            continue
                        overlap = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
                        if overlap >= bm25_threshold:
                            candidates.append((group[i].id(), group[j].id(), round(overlap, 3)))
                    except Exception:
                        logger.debug("BM25 tokenization failed for pair (%s, %s)",
                                      group[i].id(), group[j].id())
                        continue

        candidates.sort(key=lambda x: -x[2])
        return candidates

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        mem_store = ctx.mem_store
        cfg = _curator_config(mem_store)
        merge_threshold = cfg.get("similarity", {}).get("merge_threshold", 0.7)
        result = CuratorResult(action_name=self.name)

        candidates = self._scan_for_similar(mem_store, ctx)
        result.similar_pairs = len(candidates)

        is_superseded = getattr(mem_store, "is_superseded", lambda _: False)

        for id_a, id_b, score in candidates:
            if score < merge_threshold:
                continue
            try:
                mem_a = mem_store.get(id_a)
                mem_b = mem_store.get(id_b)
                if mem_a is None or mem_b is None:
                    continue
                if is_superseded(id_a) or is_superseded(id_b):
                    continue

                if mem_a.body.strip() == mem_b.body.strip():
                    to_archive = mem_a if id_a < id_b else mem_b
                    entry = build_cold_entry(to_archive, context_tag="dedup")
                    success, err = archive_and_delete(mem_store, to_archive, entry, "dedup")
                    if success:
                        result.merged += 1
                        result.journal_entries.append({
                            "action": "dedup",
                            "memory_id": to_archive.id(),
                            "context_tag": "dedup",
                            "scope_label": getattr(ctx, "scope_label", "local_global"),
                        })
                    elif err:
                        result.errors.append(f"dedup {to_archive.id()}: {err}")
                    continue

                keeper, archived = (mem_a, mem_b) if len(mem_a.body) >= len(mem_b.body) else (mem_b, mem_a)
                keeper_body = keeper.body.strip()
                archived_body = archived.body.strip()

                overlap_len = 0
                for i in range(min(200, len(archived_body)), 0, -1):
                    if keeper_body.endswith(archived_body[:i]):
                        overlap_len = i
                        break

                if overlap_len > 0:
                    merged_body = keeper_body + "\n---\n" + archived_body[overlap_len:]
                else:
                    merged_body = keeper_body + "\n---\n" + archived_body

                merged_tags = list(set((keeper.frontmatter.tags or []) + (archived.frontmatter.tags or [])))
                existing_supersedes = list(keeper.frontmatter.supersedes or [])
                new_supersedes = list(set(existing_supersedes + [archived.id()]))
                mem_store.update(keeper.id(), body=merged_body, tags=merged_tags, supersedes=new_supersedes)

                entry = build_cold_entry(
                    archived,
                    context_tag="merged",
                    supersedes=[keeper.id()],
                )
                success, err = archive_and_delete(mem_store, archived, entry, "merged")
                if success:
                    result.merged += 1
                    result.journal_entries.append({
                        "action": "merge",
                        "memory_id": archived.id(),
                        "keeper_id": keeper.id(),
                        "context_tag": "merged",
                        "scope_label": getattr(ctx, "scope_label", "local_global"),
                    })
                elif err:
                    result.errors.append(f"merge {archived.id()}: {err}")

            except Exception as e:
                result.errors.append(f"pair ({id_a},{id_b}): {e}")
                logger.warning("Curator MergeSimilar failed on pair (%s, %s): %s", id_a, id_b, e)
                continue

        return result


def get_graph_manager_compat():
    """Resolve and return the runtime graph manager, or None.

    Exposed at module level so tests can monkeypatch it for CleanOrphanEdges.
    """
    graph_mod = _lb_fn("mem_reflection_hermes.runtime.graph") if _lb_fn is not None else None
    if graph_mod is None:
        graph_mod = _lb_fn("runtime.graph") if _lb_fn is not None else None
    if graph_mod is None or not hasattr(graph_mod, "get_graph_manager_compat"):
        return None
    try:
        return graph_mod.get_graph_manager_compat()
    except Exception as e:
        logger.warning("get_graph_manager_compat failed: %s", e, exc_info=True)
        return None


class CleanOrphanEdges(CuratorAction):
    """Phase 5: remove graph edges pointing to non-existent memories."""

    name = "CleanOrphanEdges"

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        mem_store = ctx.mem_store
        result = CuratorResult(action_name=self.name)

        if getattr(ctx, "filters", None) and not getattr(ctx, "admin_global", False):
            msg = "Orphan edge cleanup skipped: scoped run without admin_global"
            result.errors.append(msg)
            logger.warning("CleanOrphanEdges %s", msg)
            return result

        try:
            gm = get_graph_manager_compat()
        except Exception as e:
            result.errors.append(f"get_graph_manager_compat: {e}")
            logger.warning("Curator CleanOrphanEdges graph manager unavailable: %s", e)
            return result
        if gm is None:
            return result

        try:
            all_ids = {m.id() for m in ctx.list_active()}
            result.orphan_edges = gm.store.clean_orphan_edges(all_ids)
            if result.orphan_edges > 0:
                result.journal_entries.append({
                    "action": "clean_orphan_edges",
                    "count": result.orphan_edges,
                    "scope_label": getattr(ctx, "scope_label", "local_global"),
                })
            # P2-4: prune invalidated typed facts older than the configured retention.
            retention_days = (
                _curator_config(mem_store)
                .get("gc", {})
                .get("typed_fact_retention_days", 30)
            )
            result.typed_facts_deleted = gm.store._gi.compact_typed_facts(retention_days)
        except Exception as e:
            result.errors.append(f"clean_orphan_edges: {e}")
            logger.warning("Curator orphan edge cleanup failed: %s", e)

        return result


class GenerateReport(CuratorAction):
    """Phase 6: aggregate results and generate text + persisted report."""

    name = "GenerateReport"

    def execute(self, ctx: CuratorContext, prior_results: Optional[List[CuratorResult]] = None) -> CuratorResult:
        """Generate report from prior action results.

        Accepts prior_results so the orchestrator can pass in accumulated
        action outputs. If omitted, defaults are used.
        """
        prior_results = prior_results or []
        mem_store = ctx.mem_store
        result = CuratorResult(action_name=self.name)

        archived_stale = sum(r.archived for r in prior_results if r.action_name == "ArchiveStale")
        archived_superseded = sum(r.archived for r in prior_results if r.action_name == "ArchiveSuperseded")
        compacted = sum(r.compacted for r in prior_results if r.action_name == "CompactChains")
        similar_pairs = sum(r.similar_pairs for r in prior_results if r.action_name == "MergeSimilar")
        merged = sum(r.merged for r in prior_results if r.action_name == "MergeSimilar")
        orphan_edges = sum(r.orphan_edges for r in prior_results if r.action_name == "CleanOrphanEdges")
        detected_stale = archived_stale

        # Intra-curator imports: safe to import normally in package mode. When
        # loaded standalone, report generation degrades gracefully.
        try:
            from .report import generate_report, _persist_report
            from .cold_store import _cold_store_path
        except ImportError:
            def generate_report(**kwargs):  # type: ignore[no-redef]
                return "No curator actions"

            def _persist_report(*args, **kwargs):  # type: ignore[no-redef]
                pass

            def _cold_store_path(mem_store):  # type: ignore[no-redef]
                from pathlib import Path
                return Path.home() / ".hermes" / "memory" / "cold_store.jsonl"

        report_text = generate_report(
            detected_stale=detected_stale,
            archived_stale=archived_stale,
            archived_superseded=archived_superseded,
            similar_pairs=similar_pairs,
            errors=list(ctx.errors),
            merged_count=merged,
            compacted_count=compacted,
            orphan_count=orphan_edges,
        )

        ctx.errors.extend(result.errors)
        result.__dict__["report_text"] = report_text
        return result
