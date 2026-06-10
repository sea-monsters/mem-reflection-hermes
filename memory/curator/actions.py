"""Curator actions — composable pipeline phases.

Each phase is a CuratorAction subclass implementing execute(ctx) -> CuratorResult.
Shared logic lives in helpers.py; cold store I/O lives in cold_store.py.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .helpers import (
    CuratorContext,
    CuratorResult,
    _curator_config,
    archive_and_delete,
    build_cold_entry,
    is_protected,
    load_last_access,
)

logger = logging.getLogger(__name__)


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
            all_active = mem_store.list_active()
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
                    eff = None
                    try:
                        from .helpers import _load_effectiveness
                        eff = _load_effectiveness(mem_store, mid)
                    except Exception:
                        pass
                    if eff:
                        score = eff.get("effectiveness", 0.5)
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
            for mem in mem_store.list_active():
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
                all_items = mem_store.list(active_only=False)
            else:
                all_items = mem_store.list_active()
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
                elif err:
                    result.errors.append(f"archive {mid}: {err}")

        return result


class MergeSimilar(CuratorAction):
    """Phase 3 + 3b: detect and merge near-duplicate memories."""

    name = "MergeSimilar"

    def _tokenise_fn(self, mem_store):
        try:
            from ...core.store import _tokenise
            return _tokenise
        except Exception:
            def _fallback(t: str) -> List[str]:
                return re.findall(r"\w+", t.lower())
            return _fallback

    def _scan_for_similar(self, mem_store) -> List[Tuple[str, str, float]]:
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

        def _sort_key(m):
            mem_id = m.id()
            try:
                from .helpers import _load_effectiveness
                eff = _load_effectiveness(mem_store, mem_id)
                if eff and "last_accessed" in eff:
                    return (0, -eff["last_accessed"])
            except Exception:
                pass
            return (1, getattr(m.frontmatter, "created", ""))

        try:
            all_active = sorted(all_active, key=_sort_key)[:500]
        except Exception:
            all_active = sorted(
                all_active,
                key=lambda m: getattr(getattr(m, "frontmatter", None), "created", ""),
                reverse=True,
            )[:500]

        tokenise = self._tokenise_fn(mem_store)
        candidates: List[Tuple[str, str, float]] = []
        seen: Set[Tuple[str, str]] = set()

        for i in range(len(all_active)):
            for j in range(i + 1, len(all_active)):
                key = tuple(sorted([all_active[i].id(), all_active[j].id()]))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    tokens_a = set(tokenise(all_active[i].body))
                    tokens_b = set(tokenise(all_active[j].body))
                    if not tokens_a or not tokens_b:
                        continue
                    overlap = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
                    if overlap >= bm25_threshold:
                        candidates.append((all_active[i].id(), all_active[j].id(), round(overlap, 3)))
                except Exception:
                    continue

        candidates.sort(key=lambda x: -x[2])
        return candidates

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        mem_store = ctx.mem_store
        cfg = _curator_config(mem_store)
        merge_threshold = cfg.get("similarity", {}).get("merge_threshold", 0.7)
        result = CuratorResult(action_name=self.name)

        candidates = self._scan_for_similar(mem_store)
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
                elif err:
                    result.errors.append(f"merge {archived.id()}: {err}")

            except Exception as e:
                result.errors.append(f"pair ({id_a},{id_b}): {e}")
                logger.warning("Curator MergeSimilar failed on pair (%s, %s): %s", id_a, id_b, e)
                continue

        return result


class CleanOrphanEdges(CuratorAction):
    """Phase 5: remove graph edges pointing to non-existent memories."""

    name = "CleanOrphanEdges"

    def execute(self, ctx: CuratorContext) -> CuratorResult:
        mem_store = ctx.mem_store
        result = CuratorResult(action_name=self.name)

        try:
            from ...runtime.graph import get_graph_manager_compat
            gm = get_graph_manager_compat()
        except Exception:
            return result
        if gm is None:
            return result

        try:
            all_ids = {m.id() for m in mem_store.list_active()}
            result.orphan_edges = gm.store.clean_orphan_edges(all_ids)
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

        from .report import generate_report, _persist_report
        from .cold_store import _cold_store_path

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

        # Persist report is orchestrator responsibility; this action only produces text
        # but we expose the result through a side-channel on the context
        ctx.errors.extend(result.errors)
        # Store the generated text on the result for the orchestrator to pick up
        result.__dict__["report_text"] = report_text
        return result
